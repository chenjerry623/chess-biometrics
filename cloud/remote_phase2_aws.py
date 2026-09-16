"""Run scripts/phase2_full_roster.py and scripts/phase2_multigame.py on a
temporary AWS EC2 instance instead of your laptop — same motivation as
cloud/remote_export_aws.py (this session's user explicitly doesn't have
the machine on around the clock), same proven mechanics reused rather
than re-invented: nohup-backgrounded remote run + poll-for-output-file,
SSH keepalive, self-healing launch retries via cloud/spot_common.py.

Data needed on the remote box: only `data/features/` and `data/models/`
— confirmed by grepping both phase2 scripts for every directory they
read. Lighter than remote_export_aws.py (no `data/insights/`, and no
100+-class CV memory spike), so this uses spot_common's regular
DEFAULT_INSTANCE_TYPE (c6i.2xlarge) rather than the export script's
memory-padded r6i.2xlarge.

Both scripts run in sequence on the SAME instance (avoids paying rsync
setup twice for the same data), outputs written to plain relative paths
under the remote checkout (no macOS-specific absolute OUT_PATH problem
to work around here, unlike export_dashboard_data.py) and pulled back
afterward.

Usage:
    export AWS_ACCESS_KEY_ID=...
    export AWS_SECRET_ACCESS_KEY=...
    python -m cloud.remote_phase2_aws
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

LOCAL_FULL_ROSTER_OUT = Path("scratchpad/phase2_dashboard/data.json")
LOCAL_MULTIGAME_OUT = Path("scratchpad/phase2_dashboard/multigame.json")
REMOTE_FULL_ROSTER_OUT = "data/phase2_full_roster.json"
REMOTE_MULTIGAME_OUT = "data/phase2_multigame.json"
SSH_TIMEOUT_S = 5400  # both scripts together; full-roster alone ran ~55 min locally


def run_remote_phase2(ec2, ip: str) -> None:
    remote_root = f"/home/{SSH_USER}/chess-clock"

    def ssh(cmd: str, timeout: int = SSH_TIMEOUT_S) -> str:
        result = subprocess.run(
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
        f"mkdir -p {remote_root}/scripts {remote_root}/src {remote_root}/data/features {remote_root}/data/models"
    )

    print("Copying code and data to remote...")
    rsync_to("scripts/", f"{remote_root}/scripts/")
    rsync_to("src/", f"{remote_root}/src/")
    rsync_to("requirements.txt", f"{remote_root}/requirements.txt")
    rsync_to("data/features/", f"{remote_root}/data/features/", timeout=1800)
    rsync_to("data/models/", f"{remote_root}/data/models/", timeout=1800)

    print("Setting up remote venv...")
    ssh(
        f"cd {remote_root} && python3 -m venv .venv && source .venv/bin/activate && pip install -q -r requirements.txt",
        timeout=600,
    )

    # Self-terminating safety net — see start_remote_watchdog's docstring.
    # Waits for BOTH scripts' output files (they run in sequence below),
    # so it won't fire between the two even if the first finishes fast.
    start_remote_watchdog(
        ssh, remote_root, [REMOTE_FULL_ROSTER_OUT, REMOTE_MULTIGAME_OUT],
        grace_minutes=10, max_hours=3,
    )

    def run_and_poll(module: str, args: str, remote_out: str, log_name: str, label: str) -> None:
        print(f"Starting {label} in the background (this is the CPU-heavy part)...")
        ssh(
            f"cd {remote_root} && source .venv/bin/activate && "
            f"(nohup python -m {module} {args} > {log_name} 2>&1 < /dev/null &) && echo started",
            timeout=600,
        )
        poll_interval = 20
        deadline = time.time() + SSH_TIMEOUT_S
        print(f"Polling every {poll_interval}s for {remote_out}...")
        while True:
            time.sleep(poll_interval)
            status = ssh(f"cd {remote_root} && test -f {remote_out} && echo DONE || echo RUNNING").strip()
            print(f"  [{label}] ...{status.lower()}")
            if status == "DONE":
                break
            if time.time() > deadline:
                log_tail = ssh(f"tail -n 80 {remote_root}/{log_name} || true")
                print(f"  --- remote {log_name} tail ---\n{log_tail}")
                raise SystemExit(f"{label} didn't finish within {SSH_TIMEOUT_S}s.")

    run_and_poll(
        "scripts.phase2_full_roster", f"--n-games 30 --out {REMOTE_FULL_ROSTER_OUT}",
        f"{remote_root}/{REMOTE_FULL_ROSTER_OUT}", "phase2_full_roster.log", "full-roster scan",
    )
    run_and_poll(
        "scripts.phase2_multigame", f"--out {REMOTE_MULTIGAME_OUT}",
        f"{remote_root}/{REMOTE_MULTIGAME_OUT}", "phase2_multigame.log", "multi-game aggregation",
    )

    print("Pulling results back...")
    LOCAL_FULL_ROSTER_OUT.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_MULTIGAME_OUT.parent.mkdir(parents=True, exist_ok=True)
    rsync_from(f"{remote_root}/{REMOTE_FULL_ROSTER_OUT}", str(LOCAL_FULL_ROSTER_OUT))
    rsync_from(f"{remote_root}/{REMOTE_MULTIGAME_OUT}", str(LOCAL_MULTIGAME_OUT))
    print(f"Wrote {LOCAL_FULL_ROSTER_OUT} and {LOCAL_MULTIGAME_OUT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE)
    parser.add_argument("--spot", action="store_true")
    parser.add_argument("--ssh-pubkey", type=Path, default=Path.home() / ".ssh" / "id_ed25519.pub")
    parser.add_argument("--keep-instance", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=15)
    parser.add_argument("--retry-delay-s", type=int, default=30)
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
        run_fn=run_remote_phase2,
        max_attempts=args.max_attempts,
        retry_delay_s=args.retry_delay_s,
        keep_instance=args.keep_instance,
        region=args.region,
        name_prefix="chess-phase2",
        spot=args.spot,
    )


if __name__ == "__main__":
    main()
