"""Run src/maia_annotate.py on a temporary AWS EC2 Spot instance, the same
way remote_annotate_aws.py already does for Stockfish annotation.

Why this exists: Maia annotation was running locally on this Mac's GPU
(lc0's Metal backend) — the only stage in the whole pipeline still tied to
one laptop being awake. That meant overnight/unattended progress stopped
whenever the laptop slept or closed. This moves it to the same
self-healing Spot infrastructure as Stockfish annotation.

The one real wrinkle vs. Stockfish: lc0 has no prebuilt Linux binary (only
Windows/Android releases — confirmed against the actual GitHub releases
list, not assumed), so the remote setup builds it from source (meson +
ninja + a C++20 compiler + OpenBLAS for the CPU backend, since these
instances have no GPU) instead of a one-line `apt-get install`. This adds
a few minutes to every fresh instance's setup — including every relaunch
after a Spot reclaim, since a reclaimed instance is gone entirely, not
just interrupted. Acceptable for now given annotation runs are hours long;
if repeated reclaims make the rebuild overhead annoying, the fix is baking
a custom AMI with lc0 pre-built rather than optimizing this script further.

Maia queries are nodes=1 (single policy-head forward pass, no real search
— see src/maia_query.py's docstring), which is cheap even on CPU-only BLAS;
this is NOT the same workload as running a deep search, so skipping GPU
instances (much more expensive, and Spot capacity for them is far less
reliable) is a deliberate, reasoned choice, not a shortcut.

Launch/retry/cleanup mechanics are shared with remote_annotate_aws.py via
cloud/spot_common.py — see that module's docstring for why.

Usage:
    export AWS_ACCESS_KEY_ID=...
    export AWS_SECRET_ACCESS_KEY=...
    python -m cloud.remote_maia_annotate --plies data/parsed/<u>_plies.parquet
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
)

sys.stdout.reconfigure(line_buffering=True)

LC0_BRANCH = "release/0.32"
MAIA_WEIGHTS_DIR = Path("data/maia_weights")


def run_remote_maia(ec2, ip: str, plies_path: Path, poll_interval: int = 60) -> None:
    remote_root = f"/home/{SSH_USER}/chess-clock"
    stem = plies_path.stem
    local_out = Path("data/maia_annotated") / f"{stem}_maia.parquet"
    partial_name = f".{stem}_maia_partial"
    local_partial = Path("data/maia_annotated") / partial_name
    remote_lc0 = f"{remote_root}/lc0/build/release/lc0"

    # See remote_annotate_aws.py's identical constant for why this exists:
    # ConnectTimeout only bounds the initial handshake, not a session that
    # connects fine and then goes silent (remote command hangs, or the
    # instance is reclaimed mid-command) — found this hang for real on
    # group3's Stockfish run (47+ minutes, instance already gone, no
    # exception ever raised to trigger a retry) and it applies identically
    # here, so fixed both at once.
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

    print("Installing build deps on remote (git, meson, ninja, OpenBLAS, compiler)...")
    ssh(
        "sudo apt-get update -q && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "
        "python3-venv python3-pip git ninja-build meson build-essential "
        "libopenblas-dev zlib1g-dev pkg-config && "
        f"mkdir -p {remote_root}/src {remote_root}/data/parsed {remote_root}/data/maia_annotated {remote_root}/data/maia_weights"
    )

    print(f"Cloning and building lc0 ({LC0_BRANCH}) on remote — this takes a few minutes...")
    ssh(
        f"test -x {remote_lc0} || "
        f"(cd {remote_root} && git clone -b {LC0_BRANCH} --depth 1 https://github.com/LeelaChessZero/lc0.git && "
        f"cd lc0 && ./build.sh)",
        timeout=1200,
    )
    print("  lc0 build ready.")

    print("Copying code and data to remote...")
    rsync_to("src/", f"{remote_root}/src/")
    rsync_to("requirements.txt", f"{remote_root}/requirements.txt")
    rsync_to(str(plies_path), f"{remote_root}/data/parsed/{plies_path.name}")
    rsync_to(f"{MAIA_WEIGHTS_DIR}/", f"{remote_root}/data/maia_weights/")
    if local_out.exists():
        print(f"  found existing output {local_out}, uploading to resume from it")
        rsync_to(str(local_out), f"{remote_root}/data/maia_annotated/{local_out.name}")
    if local_partial.exists():
        print(f"  found existing local checkpoint dir {local_partial}, uploading to resume from it")
        rsync_to(f"{local_partial}/", f"{remote_root}/data/maia_annotated/{partial_name}/")

    print("Setting up remote venv and starting maia_annotate.py in the background...")
    ssh(
        f"cd {remote_root} && python3 -m venv .venv && source .venv/bin/activate && "
        f"pip install -q -r requirements.txt && "
        f"(nohup python -m src.maia_annotate --plies data/parsed/{plies_path.name} "
        f"--engine {remote_lc0} > maia_annotate.log 2>&1 < /dev/null &) && echo started"
    )

    Path("data/maia_annotated").mkdir(parents=True, exist_ok=True)
    print(f"Polling every {poll_interval}s, syncing back partial progress each time...")
    while True:
        time.sleep(poll_interval)
        try:
            rsync_from(f"{remote_root}/data/maia_annotated/{partial_name}/", f"{local_partial}/", check=False)
            status = ssh(
                f"test -f {remote_root}/data/maia_annotated/{local_out.name} && echo DONE || echo RUNNING"
            ).strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(
                "Lost SSH connection to the instance (or it stopped responding) — likely a Spot "
                f"reclaim. Progress synced through the last successful poll is safe in {local_partial}/. "
                "Re-run this command; maia_annotate.py resumes from those checkpoints on the next attempt."
            )
            raise SystemExit(1)
        print(f"  ...{status.lower()}")
        if status == "DONE":
            break

    print("Pulling final Maia-annotated result back...")
    rsync_from(f"{remote_root}/data/maia_annotated/{local_out.name}", str(local_out))
    print(f"Wrote {local_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
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
        run_fn=lambda ec2, ip: run_remote_maia(ec2, ip, args.plies),
        max_attempts=args.max_attempts,
        retry_delay_s=args.retry_delay_s,
        keep_instance=args.keep_instance,
        region=args.region,
        name_prefix="chess-maia",
    )


if __name__ == "__main__":
    main()
