"""Run annotate.py on a temporary Hetzner Cloud server instead of your laptop.

Stockfish annotation is pure CPU, multi-hour, and embarrassingly parallel —
a bad fit for a laptop you also need for other things (it competes for
cores, and pauses every time the machine sleeps or locks). This script
rents a high-core-count box only for the duration of one annotate.py pass:

  1. Create a server via the Hetzner Cloud API.
  2. Wait for it to boot and accept SSH.
  3. rsync over src/, requirements.txt, and the one parsed plies parquet
     you're annotating (plus any existing annotated parquet, so a partial
     local run resumes instead of re-paying for work already done).
  4. Install Python deps + Stockfish, run annotate.py with --workers set to
     the server's core count.
  5. rsync the finished (or partially finished, if interrupted) annotated
     parquet back to your local data/annotated/.
  6. Delete the server, so billing stops.

One-time setup (can't be scripted — needs your own account):
  1. Create a Hetzner Cloud account: https://console.hetzner.cloud
  2. Create a Project (any name).
  3. Project -> Security -> API Tokens -> Generate (Read & Write).
     Export it: export HETZNER_API_TOKEN=...
  4. Project -> Security -> SSH Keys -> Add your local public key
     (~/.ssh/id_ed25519.pub or id_rsa.pub). Note the name you gave it.

Usage:
    export HETZNER_API_TOKEN=...
    python -m cloud.remote_annotate \
        --plies data/parsed/<u>_plies.parquet \
        --ssh-key-name <name you gave the key in step 4> \
        --depth 12

First run against a real account will probably need small fixes (image
names / API details can drift) — treat this as a working draft, not a
guarantee.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

# See remote_annotate_aws.py for why: fully-buffered stdout when redirected
# to a file can hide progress prints for many minutes on a long-running job.
sys.stdout.reconfigure(line_buffering=True)

API = "https://api.hetzner.cloud/v1"
IMAGE = "ubuntu-24.04"
# Dedicated-vCPU line: predictable throughput for a CPU-bound batch job,
# unlike shared-vCPU types which can be throttled by noisy neighbors.
DEFAULT_SERVER_TYPE = "ccx33"  # 8 dedicated vCPUs, 32GB RAM
DEFAULT_LOCATION = "ash"  # Ashburn, VA — swap for "fsn1" (Germany) etc. if closer


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_server(token: str, name: str, server_type: str, ssh_key_name: str, location: str) -> dict:
    keys = requests.get(f"{API}/ssh_keys", headers=_headers(token), timeout=30).json()["ssh_keys"]
    match = [k for k in keys if k["name"] == ssh_key_name]
    if not match:
        names = [k["name"] for k in keys]
        raise SystemExit(f"No SSH key named '{ssh_key_name}' in this project. Have: {names}")
    ssh_key_id = match[0]["id"]

    resp = requests.post(
        f"{API}/servers",
        headers=_headers(token),
        json={
            "name": name,
            "server_type": server_type,
            "image": IMAGE,
            "location": location,
            "ssh_keys": [ssh_key_id],
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise SystemExit(f"Server creation failed: {resp.status_code} {resp.text}")
    return resp.json()["server"]


def wait_for_ip_and_ssh(token: str, server_id: int, ip: str | None, timeout_s: int = 300) -> str:
    if ip is None:
        for _ in range(60):
            server = requests.get(f"{API}/servers/{server_id}", headers=_headers(token), timeout=30).json()["server"]
            ip = server["public_net"]["ipv4"]["ip"]
            if ip:
                break
            time.sleep(5)
        if not ip:
            raise SystemExit("Server never got a public IP — check the Hetzner console.")

    print(f"Server IP: {ip} — waiting for SSH...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
             f"root@{ip}", "echo ready"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("SSH is up.")
            return ip
        time.sleep(5)
    raise SystemExit(f"SSH never came up within {timeout_s}s on {ip}.")


def run_remote(ip: str, plies_path: Path, depth: int, workers: int) -> None:
    remote_root = "/root/chess-clock"
    stem = plies_path.stem
    local_annotated = Path("data/annotated") / f"{stem}_d{depth}.parquet"

    def ssh(cmd: str) -> None:
        subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}", cmd], check=True)

    def rsync_to(src: str, dst: str) -> None:
        subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", src, f"root@{ip}:{dst}"], check=True)

    def rsync_from(src: str, dst: str) -> None:
        subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no", f"root@{ip}:{src}", dst], check=True)

    print("Installing dependencies on remote...")
    ssh(
        "apt-get update -q && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip stockfish && "
        f"mkdir -p {remote_root}/src {remote_root}/data/parsed {remote_root}/data/annotated"
    )

    print("Copying code and data to remote...")
    rsync_to("src/", f"{remote_root}/src/")
    rsync_to("requirements.txt", f"{remote_root}/requirements.txt")
    rsync_to(str(plies_path), f"{remote_root}/data/parsed/{plies_path.name}")
    if local_annotated.exists():
        print(f"  found existing partial {local_annotated}, uploading to resume from it")
        rsync_to(str(local_annotated), f"{remote_root}/data/annotated/{local_annotated.name}")

    print(f"Running annotate.py remotely (depth={depth}, workers={workers})...")
    ssh(
        f"cd {remote_root} && python3 -m venv .venv && source .venv/bin/activate && "
        f"pip install -q -r requirements.txt && "
        f"python -m src.annotate --plies data/parsed/{plies_path.name} --depth {depth} --workers {workers}"
    )

    print("Pulling annotated result back...")
    Path("data/annotated").mkdir(parents=True, exist_ok=True)
    rsync_from(f"{remote_root}/data/annotated/{local_annotated.name}", str(local_annotated))
    print(f"Wrote {local_annotated}")


def delete_server(token: str, server_id: int) -> None:
    requests.delete(f"{API}/servers/{server_id}", headers=_headers(token), timeout=30)
    print("Server deleted — billing stopped.")


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--server-type", default=DEFAULT_SERVER_TYPE)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--ssh-key-name", required=True)
    parser.add_argument("--keep-server", action="store_true",
                         help="don't delete the server when done (for debugging)")
    args = parser.parse_args()

    token = os.environ.get("HETZNER_API_TOKEN")
    if not token:
        raise SystemExit("Set HETZNER_API_TOKEN first (Project -> Security -> API Tokens).")

    # Core count per Hetzner server type (dedicated-vCPU ccx line).
    workers_by_type = {"ccx13": 2, "ccx23": 4, "ccx33": 8, "ccx43": 16, "ccx53": 32}
    workers = workers_by_type.get(args.server_type, 8)

    name = f"chess-annotate-{int(time.time())}"
    print(f"Creating server '{name}' ({args.server_type} in {args.location})...")
    server = create_server(token, name, args.server_type, args.ssh_key_name, args.location)
    server_id = server["id"]
    ip = server["public_net"]["ipv4"]["ip"]

    try:
        ip = wait_for_ip_and_ssh(token, server_id, ip)
        run_remote(ip, args.plies, args.depth, workers)
    finally:
        if args.keep_server:
            print(f"--keep-server passed: leaving '{name}' running. Remember to delete it manually!")
        else:
            delete_server(token, server_id)


if __name__ == "__main__":
    main()
