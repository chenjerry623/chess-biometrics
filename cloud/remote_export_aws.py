"""Run scripts/export_dashboard_data.py on a temporary AWS EC2 Spot
instance instead of your laptop.

Why this exists: the identification model's cross-validation
(HistGradientBoostingClassifier over 100+ classes) pins every CPU core for
several minutes — loud fan, hot laptop, and it competes with everything
else you're doing on the machine. Unlike Stockfish/Maia annotation, this
job is short (single-digit minutes, not hours), so there's no need for the
polling/checkpoint-sync loop the other two remote scripts use — just run
the command over SSH and wait for it to finish, then pull back the one
output file.

Data needed on the remote box: `data/features/`, `data/insights/`, and
`data/models/` (confirmed by grepping export_dashboard_data.py for every
directory it reads — nothing else). Those three directories are ~1.35GB
combined as of the 126-player pool; rsync only re-transfers what changed
on repeat runs. `OUT_PATH` in export_dashboard_data.py is a hardcoded
absolute path (this session's local scratchpad) — rather than editing the
script, `mkdir -p` on the remote recreates that exact path, the script
writes to it same as locally, and this script rsyncs that one file back
afterward. No code changes needed in export_dashboard_data.py itself.

Launch/retry/cleanup mechanics are shared with the other cloud/remote_*.py
scripts via cloud/spot_common.py — see that module's docstring for why.

Usage:
    export AWS_ACCESS_KEY_ID=...
    export AWS_SECRET_ACCESS_KEY=...
    python -m cloud.remote_export_aws
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import boto3

from cloud.spot_common import (
    DEFAULT_REGION,
    DEFAULT_INSTANCE_TYPE,
    SSH_USER,
    ensure_key_pair,
    ensure_security_group,
    latest_ubuntu_ami,
    my_public_ip,
    run_self_healing,
    start_remote_watchdog,
)

sys.stdout.reconfigure(line_buffering=True)

# The exact OUT_PATH from scripts/export_dashboard_data.py — kept in sync
# manually since that script hardcodes it rather than taking it as an arg.
OUT_PATH = Path(
    "/private/tmp/claude-501/-Users-jerrychen-Projects-chess-clock/"
    "8b9bcf87-b66e-4b98-9e9b-bba7f14095e2/scratchpad/dashboard/data.json"
)
SSH_TIMEOUT_S = 1800  # generous — identification CV over 100+ classes is the long pole


def run_remote_export(ec2, ip: str) -> None:
    remote_root = f"/home/{SSH_USER}/chess-clock"

    def ssh(cmd: str, timeout: int = SSH_TIMEOUT_S) -> str:
        result = subprocess.run(
            # ServerAliveInterval/CountMax: this script's export run is a
            # single blocking SSH call that sits SILENT for several minutes
            # (unlike remote_annotate_aws.py/remote_maia_annotate.py, which
            # only ever make short-lived polling calls) — exactly the
            # pattern that intermediate network equipment/NAT drops for
            # having no traffic. Confirmed on a real run: the export died
            # mid-computation with a bare "exit status 255" and no other
            # explanation. Keepalive pings every 30s, tolerating up to 10
            # missed replies (5 min) before giving up, so a real network
            # blip doesn't masquerade as this.
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10", f"{SSH_USER}@{ip}", cmd],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return result.stdout

    def rsync_to(src: str, dst: str, timeout: int = 600) -> None:
        subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", src, f"{SSH_USER}@{ip}:{dst}"], check=True, timeout=timeout)

    def rsync_from(src: str, dst: str, timeout: int = 120) -> None:
        subprocess.run(
            ["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", f"{SSH_USER}@{ip}:{src}", dst],
            check=True, timeout=timeout,
        )

    print("Installing dependencies on remote...")
    ssh(
        "sudo apt-get update -q && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip && "
        f"mkdir -p {remote_root}/scripts {remote_root}/src {remote_root}/data/raw "
        f"{remote_root}/data/features {remote_root}/data/insights {remote_root}/data/models"
    )

    print("Copying code and data to remote (features/insights/models — this can take a few minutes the first time)...")
    rsync_to("scripts/", f"{remote_root}/scripts/")
    rsync_to("src/", f"{remote_root}/src/")
    rsync_to("requirements.txt", f"{remote_root}/requirements.txt")
    rsync_to("data/features/", f"{remote_root}/data/features/", timeout=1800)
    rsync_to("data/insights/", f"{remote_root}/data/insights/", timeout=1800)
    rsync_to("data/models/", f"{remote_root}/data/models/", timeout=1800)
    # Just the one small live-ratings cache, NOT the rest of data/raw/ (raw
    # PGNs are gigabytes and build_profile()/build_identification() never
    # read them — only current_ratings.json). Missed entirely on the first
    # run after this file was added: build_profile() silently fell back to
    # the stale last-game-header rating for every player because the file
    # simply didn't exist on the remote box, no error, just the old number.
    current_ratings = Path("data/raw/current_ratings.json")
    if current_ratings.exists():
        rsync_to(str(current_ratings), f"{remote_root}/data/raw/current_ratings.json")

    print("Recreating the output directory (export_dashboard_data.py's OUT_PATH is a hardcoded absolute path)...")
    # OUT_PATH mirrors a macOS-specific path (/private/tmp/...) — on Ubuntu,
    # `/private` doesn't exist and the ubuntu user has no permission to
    # create a new top-level directory under `/` without sudo. Confirmed
    # the hard way: a bare `mkdir -p` here failed with exit status 1 on a
    # real run. sudo + chown so the later rsync/python write (running as
    # plain ubuntu) can actually write into it.
    ssh(f"sudo mkdir -p {OUT_PATH.parent} && sudo chown -R {SSH_USER}:{SSH_USER} /private")

    # Run the export itself in the BACKGROUND and poll for completion,
    # rather than one long blocking SSH call that sits silent for the
    # several minutes the identification CV takes. Confirmed the hard way,
    # twice, even after adding SSH keepalive: that long-silent pattern was
    # dying mid-run with an empty-stdout/stderr "exit status 255" —  a raw
    # connection drop, not a script error (the diagnostic printing added to
    # spot_common.py's run_self_healing confirmed there was nothing to
    # print). remote_annotate_aws.py and remote_maia_annotate.py never hit
    # this because they only ever make SHORT polling calls — adopting that
    # same proven pattern here instead of continuing to tune SSH options
    # blindly against a failure mode that keepalive alone didn't fix.
    #
    # venv creation + pip install + the nohup launch all happen in ONE ssh
    # call here — deliberately, matching remote_annotate_aws.py's/
    # remote_maia_annotate.py's proven structure exactly. An earlier
    # version of this script split setup (venv+pip) and the nohup launch
    # into two SEPARATE ssh() calls, and the backgrounded process was
    # confirmed (via a direct `ps aux` on the instance) to not exist at
    # all afterward — no crash, no error, just never there — while running
    # the exact same command as one combined foreground call worked fine.
    # Never fully isolated WHY the split caused it (possibly something
    # about nohup's detachment depending on originating in the same shell
    # that also did the venv setup), but matching the pattern that has
    # never once failed across dozens of runs this session was the
    # pragmatic fix over further guessing.
    print("Setting up remote venv and starting the export in the background (this is the CPU-heavy part)...")
    ssh(
        f"cd {remote_root} && python3 -m venv .venv && source .venv/bin/activate && "
        f"pip install -q -r requirements.txt && "
        f"(nohup python -m scripts.export_dashboard_data > export.log 2>&1 < /dev/null &) && echo started",
        timeout=600,
    )

    # Self-terminating safety net: if THIS local polling loop dies
    # (laptop closed, network drop) before the loop below reaches its own
    # cleanup, the instance still shuts itself down instead of idling and
    # billing indefinitely. See start_remote_watchdog's docstring.
    start_remote_watchdog(ssh, remote_root, [str(OUT_PATH)], grace_minutes=10, max_hours=2)

    poll_interval = 20
    deadline = time.time() + SSH_TIMEOUT_S
    print(f"Polling every {poll_interval}s for {OUT_PATH.name}...")
    while True:
        time.sleep(poll_interval)
        status = ssh(f"test -f {OUT_PATH} && echo DONE || echo RUNNING").strip()
        print(f"  ...{status.lower()}")
        if status == "DONE":
            break
        if time.time() > deadline:
            log_tail = ssh(f"tail -n 60 {remote_root}/export.log || true")
            print(f"  --- remote export.log tail ---\n{log_tail}")
            raise SystemExit(f"Export didn't finish within {SSH_TIMEOUT_S}s.")

    print("Pulling data.json back...")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rsync_from(str(OUT_PATH), str(OUT_PATH))
    print(f"Wrote {OUT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    # NOT spot_common's shared DEFAULT_INSTANCE_TYPE (c6i.2xlarge, 16GB RAM,
    # tuned for the CPU-bound annotation jobs). Confirmed via dmesg/syslog on
    # a real run: the identification model's CV over the 126-player pool
    # (100+ classes) pushed the export process to 15.6GB anon-rss and the
    # kernel OOM-killer silently SIGKILL'd it — no Python traceback, no
    # exception for run_self_healing to catch, just an instantly-empty
    # export.log and no process in `ps aux` afterward. That silence is
    # exactly what three separate rounds of SSH/nohup fixes were chasing
    # this session, on the wrong hypothesis. r6i.2xlarge keeps the same 8
    # vCPUs but has 64GB RAM, 4x the headroom this workload actually needs.
    parser.add_argument("--instance-type", default="r6i.2xlarge")
    parser.add_argument("--spot", action="store_true",
                         help="use Spot pricing instead of on-demand (default: on-demand, since this "
                              "job is short enough that the price gap is trivial and on-demand sidesteps "
                              "Spot capacity shortages entirely — confirmed real on a day when Spot "
                              "exhausted every retry across two instance sizes without once succeeding)")
    parser.add_argument("--ssh-pubkey", type=Path, default=Path.home() / ".ssh" / "id_ed25519.pub")
    parser.add_argument("--keep-instance", action="store_true",
                         help="don't terminate the instance when done (for debugging)")
    parser.add_argument("--max-attempts", type=int, default=15,
                         help="auto-relaunch this many times total on a launch/setup failure before giving up")
    parser.add_argument("--retry-delay-s", type=int, default=30,
                         help="seconds to wait before retrying after a launch failure (capacity/quota)")
    args = parser.parse_args()

    if not args.ssh_pubkey.exists():
        raise SystemExit(
            f"No public key at {args.ssh_pubkey}. Pass --ssh-pubkey, or generate one with "
            f"'ssh-keygen -t ed25519'."
        )

    ec2 = boto3.client("ec2", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)

    my_ip = my_public_ip()
    ami_id = latest_ubuntu_ami(ssm)
    key_name = ensure_key_pair(ec2, args.ssh_pubkey)
    sg_id = ensure_security_group(ec2, my_ip)

    run_self_healing(
        ec2=ec2,
        ami_id=ami_id,
        instance_type=args.instance_type,
        key_name=key_name,
        sg_id=sg_id,
        run_fn=run_remote_export,
        max_attempts=args.max_attempts,
        retry_delay_s=args.retry_delay_s,
        keep_instance=args.keep_instance,
        region=args.region,
        name_prefix="chess-export",
        spot=args.spot,
    )


if __name__ == "__main__":
    main()
