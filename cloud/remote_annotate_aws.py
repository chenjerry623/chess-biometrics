"""Run annotate.py on a temporary AWS EC2 Spot instance instead of your laptop.

Same idea as remote_annotate_hetzner.py — rent a high-core-count box only for
the duration of one annotate.py pass — but on AWS, using EC2 Spot Instances
(~70-90% off on-demand pricing). Spot capacity can be reclaimed with ~2
minutes' notice, which is fine here: annotate.py now checkpoints each
worker chunk to disk as it finishes (see src/annotate.py's partial-chunk
directory), so a reclaimed instance loses at most one in-flight chunk, not
the whole run. Re-running this script (or the plain local command) picks up
exactly where it left off.

  1. Look up the latest Ubuntu 24.04 AMI for your region (via SSM — never a
     hardcoded, possibly-stale AMI ID).
  2. Import your local SSH public key into EC2 (skips if already present).
  3. Create/reuse a security group that allows SSH only from your current
     public IP — not the world.
  4. Launch a Spot instance.
  5. Wait for it to boot and accept SSH.
  6. rsync over src/, requirements.txt, the plies parquet you're annotating,
     and any existing (partial) annotated parquet, so local progress isn't
     re-paid for.
  7. Install deps + Stockfish, run annotate.py with --workers set to the
     instance's vCPU count.
  8. rsync the result back to your local data/annotated/.
  9. Terminate the instance — billing stops immediately.

Launch/retry/cleanup mechanics (AMI lookup, key pair, security group, the
self-healing relaunch-on-failure loop) live in cloud/spot_common.py, shared
with remote_maia_annotate.py — see that module's docstring for why.

One-time setup (can't be scripted — needs your own account):
  1. Create an AWS account: https://aws.amazon.com
  2. IAM -> Users -> Create user (NOT the root account) -> Attach a policy
     with (at minimum) ec2:RunInstances, ec2:TerminateInstances,
     ec2:DescribeInstances, ec2:CreateSecurityGroup,
     ec2:AuthorizeSecurityGroupIngress, ec2:DescribeSecurityGroups,
     ec2:ImportKeyPair, ec2:DescribeKeyPairs, ec2:CreateTags,
     ssm:GetParameter.
  3. That user -> Security credentials -> Create access key -> "Command
     Line Interface (CLI)". Export the two values it gives you:
       export AWS_ACCESS_KEY_ID=...
       export AWS_SECRET_ACCESS_KEY=...
  4. Billing -> Budgets -> Create a budget (e.g. $10/month) with an email
     alert BEFORE running anything real. This is the guardrail against a
     surprise bill — do this before step 5, not after.

Usage:
    export AWS_ACCESS_KEY_ID=...
    export AWS_SECRET_ACCESS_KEY=...
    python -m cloud.remote_annotate_aws \
        --plies data/parsed/<u>_plies.parquet --depth 12

First run against a real account will probably need small fixes (AMI
parameter paths / API details can drift) — treat this as a working draft,
not a guarantee.
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
    VCPUS_BY_TYPE,
    ensure_key_pair,
    ensure_security_group,
    latest_ubuntu_ami,
    my_public_ip,
    run_self_healing,
)

# Long-running: when stdout is redirected to a file (not a TTY, e.g. run in
# the background), Python defaults to full buffering, so prints can sit
# invisible for many minutes rather than showing progress as it happens.
sys.stdout.reconfigure(line_buffering=True)


def run_remote(ec2, ip: str, plies_path: Path, depth: int, workers: int, poll_interval: int = 60) -> None:
    """Run annotate.py on the remote box in the background and poll for
    completion, syncing back its partial-chunk checkpoint directory on every
    poll. A Spot instance CAN be reclaimed mid-run (this has actually
    happened) — without this, the only thing synced back is the final
    output, so a reclaim loses the entire run's progress, not just what
    ran since the last checkpoint. Polling and syncing partials bounds the
    loss to one poll interval."""
    remote_root = f"/home/{SSH_USER}/chess-clock"
    stem = plies_path.stem
    local_annotated = Path("data/annotated") / f"{stem}_d{depth}.parquet"
    partial_name = f".{stem}_d{depth}_partial"
    local_partial = Path("data/annotated") / partial_name

    # ConnectTimeout only bounds the initial TCP handshake — an ssh session
    # that connects fine and then goes silent (the remote command hangs, OR
    # the instance is Spot-reclaimed mid-command, leaving an established
    # session with nothing on the other end) blocks subprocess.run()
    # forever with no error, ever, from ConnectTimeout alone. Confirmed the
    # hard way: group3's setup ssh() call hung for 47+ minutes with the
    # instance already gone — no exception ever fired, so the self-healing
    # retry loop (which only triggers on a raised exception) never got the
    # chance to retry, defeating its whole purpose. `timeout=` on
    # subprocess.run raises TimeoutExpired, which the broad except in
    # spot_common.run_self_healing already treats as retryable — this was
    # the one gap in an otherwise-hardened retry chain.
    DEFAULT_SSH_TIMEOUT_S = 300

    def ssh(cmd: str, timeout: int = DEFAULT_SSH_TIMEOUT_S) -> str:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"{SSH_USER}@{ip}", cmd],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return result.stdout

    def rsync_to(src: str, dst: str, timeout: int = DEFAULT_SSH_TIMEOUT_S) -> None:
        subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", src, f"{SSH_USER}@{ip}:{dst}"], check=True, timeout=timeout)

    def rsync_from(src: str, dst: str, check: bool = True, timeout: int = DEFAULT_SSH_TIMEOUT_S) -> None:
        subprocess.run(
            ["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", f"{SSH_USER}@{ip}:{src}", dst],
            check=check, timeout=timeout,
        )

    print("Installing dependencies on remote...")
    ssh(
        "sudo apt-get update -q && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip stockfish && "
        f"mkdir -p {remote_root}/src {remote_root}/data/parsed {remote_root}/data/annotated"
    )

    print("Copying code and data to remote...")
    rsync_to("src/", f"{remote_root}/src/")
    rsync_to("requirements.txt", f"{remote_root}/requirements.txt")
    rsync_to(str(plies_path), f"{remote_root}/data/parsed/{plies_path.name}")
    if local_annotated.exists():
        print(f"  found existing annotated file {local_annotated}, uploading to resume from it")
        rsync_to(str(local_annotated), f"{remote_root}/data/annotated/{local_annotated.name}")
    if local_partial.exists():
        print(f"  found existing local checkpoint dir {local_partial}, uploading to resume from it")
        rsync_to(f"{local_partial}/", f"{remote_root}/data/annotated/{partial_name}/")

    print(f"Setting up remote venv and starting annotate.py in the background (depth={depth}, workers={workers})...")
    # A bare trailing "&" backgrounds the WHOLE preceding "&&" chain, not just
    # the final command — venv creation and pip install would silently race
    # in the background too, with the ssh call returning before either was
    # confirmed to succeed. Wrapping just the nohup launch in a subshell
    # "( ... & )" backgrounds only that, while venv/pip install still run
    # synchronously and their failures still propagate.
    ssh(
        f"cd {remote_root} && python3 -m venv .venv && source .venv/bin/activate && "
        f"pip install -q -r requirements.txt && "
        f"(nohup python -m src.annotate --plies data/parsed/{plies_path.name} --depth {depth} "
        f"--workers {workers} > annotate.log 2>&1 < /dev/null &) && echo started"
    )

    Path("data/annotated").mkdir(parents=True, exist_ok=True)
    print(f"Polling every {poll_interval}s, syncing back partial progress each time...")
    while True:
        time.sleep(poll_interval)
        try:
            rsync_from(f"{remote_root}/data/annotated/{partial_name}/", f"{local_partial}/", check=False)
            status = ssh(
                f"test -f {remote_root}/data/annotated/{local_annotated.name} && echo DONE || echo RUNNING"
            ).strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(
                "Lost SSH connection to the instance (or it stopped responding) — likely a Spot "
                f"reclaim. Progress synced through the last successful poll is safe in {local_partial}/. "
                "Re-run this command; annotate.py resumes from those checkpoints on the next attempt."
            )
            raise SystemExit(1)
        print(f"  ...{status.lower()}")
        if status == "DONE":
            break

    print("Pulling final annotated result back...")
    rsync_from(f"{remote_root}/data/annotated/{local_annotated.name}", str(local_annotated))
    print(f"Wrote {local_annotated}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE)
    parser.add_argument("--ssh-pubkey", type=Path, default=Path.home() / ".ssh" / "id_ed25519.pub")
    parser.add_argument("--keep-instance", action="store_true",
                         help="don't terminate the instance when done (for debugging)")
    parser.add_argument("--max-attempts", type=int, default=15,
                         help="auto-relaunch this many times total on a Spot reclaim or launch failure before giving up")
    parser.add_argument("--retry-delay-s", type=int, default=30,
                         help="seconds to wait before retrying after a launch failure (capacity/quota)")
    args = parser.parse_args()

    if not args.ssh_pubkey.exists():
        raise SystemExit(
            f"No public key at {args.ssh_pubkey}. Pass --ssh-pubkey, or generate one with "
            f"'ssh-keygen -t ed25519'."
        )

    workers = VCPUS_BY_TYPE.get(args.instance_type, 8)
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
        run_fn=lambda ec2, ip: run_remote(ec2, ip, args.plies, args.depth, workers),
        max_attempts=args.max_attempts,
        retry_delay_s=args.retry_delay_s,
        keep_instance=args.keep_instance,
        region=args.region,
        name_prefix="chess-annotate",
    )


if __name__ == "__main__":
    main()
