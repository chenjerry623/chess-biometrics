"""Shared AWS EC2 Spot plumbing for the cloud/remote_*_aws.py scripts —
factored out of remote_annotate_aws.py once a second script
(remote_maia_annotate.py) needed the exact same launch/retry machinery.

Deliberately factored out rather than duplicated: the self-healing retry
loop in `run_self_healing` has already needed two real bug fixes (see
PROJECT_LOG.md, "Spot resilience round 3/4") after two different uncaught
exception types silently killed unattended overnight jobs. Two copies of
this logic would mean fixing it twice, and the second copy silently
drifting out of sync is exactly the kind of bug that's invisible until a
job dies at 3am. One copy, imported by both scripts.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

import requests
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"
DEFAULT_INSTANCE_TYPE = "c6i.2xlarge"  # 8 vCPUs, compute-optimized
SSH_USER = "ubuntu"
SG_NAME = "chess-clock-annotate"
KEY_NAME = "chess-clock-annotate-key"

# vCPU count per instance type. annotate.py uses this directly for
# --workers; maia_annotate.py doesn't scale by vCPU count the same way
# (see remote_maia_annotate.py) but the lookup is still useful for sizing.
VCPUS_BY_TYPE = {
    "t2.micro": 1, "t3.micro": 1,
    "c6i.xlarge": 4, "c6i.2xlarge": 8, "c6i.4xlarge": 16, "c6i.8xlarge": 32,
    "c6i.12xlarge": 48, "c6i.16xlarge": 64, "c6i.24xlarge": 96, "c6i.32xlarge": 128,
}


def my_public_ip() -> str:
    return requests.get("https://checkip.amazonaws.com", timeout=10).text.strip()


def latest_ubuntu_ami(ssm_client) -> str:
    param = (
        "/aws/service/canonical/ubuntu/server/24.04/stable/current/"
        "amd64/hvm/ebs-gp3/ami-id"
    )
    return ssm_client.get_parameter(Name=param)["Parameter"]["Value"]


def ensure_key_pair(ec2, ssh_pubkey_path: Path) -> str:
    existing = ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [KEY_NAME]}])
    if existing["KeyPairs"]:
        return KEY_NAME
    pubkey = ssh_pubkey_path.read_text()
    ec2.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=pubkey.encode())
    print(f"Imported SSH key as '{KEY_NAME}'")
    return KEY_NAME


def ensure_security_group(ec2, my_ip: str) -> str:
    existing = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}]
    )
    if existing["SecurityGroups"]:
        sg_id = existing["SecurityGroups"][0]["GroupId"]
    else:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        sg = ec2.create_security_group(
            GroupName=SG_NAME, Description="SSH for chess-clock annotate jobs", VpcId=vpc_id
        )
        sg_id = sg["GroupId"]
        print(f"Created security group {sg_id}")

    # Keep the SSH rule pinned to whoever is running this right now, rather
    # than accumulating stale IPs or leaving it open to the world.
    try:
        rules = ec2.describe_security_group_rules(
            Filters=[{"Name": "group-id", "Values": [sg_id]}]
        )["SecurityGroupRules"]
        for r in rules:
            if not r["IsEgress"]:
                ec2.revoke_security_group_ingress(
                    GroupId=sg_id, SecurityGroupRuleIds=[r["SecurityGroupRuleId"]]
                )
    except Exception:
        pass  # no existing inbound rules to clear on a fresh group

    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": f"{my_ip}/32", "Description": "SSH from operator"}],
        }],
    )
    print(f"Security group {sg_id} now allows SSH only from {my_ip}")
    return sg_id


def launch_spot_instance(ec2, ami_id: str, instance_type: str, key_name: str, sg_id: str, name_prefix: str = "chess-annotate", spot: bool = True) -> dict:
    # `spot=False` requests a normal on-demand instance instead. Exists for
    # remote_export_aws.py specifically: that job runs for single-digit
    # minutes, so the on-demand/Spot price gap is a few cents, not dollars
    # — trivial next to burning through all 15 retry attempts on a genuine
    # regional Spot capacity shortage (confirmed for real: one run failed
    # every single attempt on InsufficientInstanceCapacity across TWO
    # different instance sizes in the same family, never once got an
    # instance). The long-running annotation jobs should stay on Spot,
    # where the discount over hours actually matters and reclaim-tolerance
    # is already built into them.
    market_options = {}
    if spot:
        market_options = {
            "InstanceMarketOptions": {
                "MarketType": "spot",
                "SpotOptions": {"SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"},
            }
        }
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        KeyName=key_name,
        SecurityGroupIds=[sg_id],
        MinCount=1,
        MaxCount=1,
        # Makes an OS-level `sudo shutdown -h now` on the instance actually
        # TERMINATE it (not just stop, which would keep billing the EBS
        # volume indefinitely) — the other half of start_remote_watchdog()
        # below. Without this, a self-issued shutdown just stops the box.
        InstanceInitiatedShutdownBehavior="terminate",
        **market_options,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": f"{name_prefix}-{int(time.time())}"}],
        }],
    )
    return resp["Instances"][0]


def wait_for_running_and_ssh(ec2, instance_id: str, timeout_s: int = 300) -> str:
    print("Waiting for instance to reach 'running'...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    desc = ec2.describe_instances(InstanceIds=[instance_id])
    instance = desc["Reservations"][0]["Instances"][0]
    ip = instance["PublicIpAddress"]
    print(f"Instance running at {ip} — waiting for SSH...")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
             f"{SSH_USER}@{ip}", "echo ready"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            print("SSH is up.")
            return ip
        time.sleep(5)
    raise SystemExit(f"SSH never came up within {timeout_s}s on {ip}.")


def start_remote_watchdog(
    ssh_fn: Callable[[str], str], remote_root: str, output_files: list[str],
    grace_minutes: int = 10, max_hours: int = 3,
) -> None:
    """Self-terminating safety net for unattended runs. Nohup's a small
    background loop ON THE INSTANCE that shuts it down once EITHER every
    path in `output_files` exists (plus a `grace_minutes` grace period,
    giving the local polling loop time to rsync results back and
    terminate the normal way first) OR `max_hours` have passed, whichever
    comes first — combined with InstanceInitiatedShutdownBehavior=
    "terminate" on launch_spot_instance() above, an OS-level `shutdown`
    here becomes a real EC2 termination, not just a stop (which would
    keep billing the EBS volume forever).

    Why this exists: the normal cleanup path (ec2.terminate_instances in
    run_self_healing's `finally` block below) only runs if the LOCAL
    polling loop that launched this instance is still alive when the job
    finishes — entirely reasonable for a job that takes a few minutes,
    but this session's user explicitly can't guarantee their laptop
    stays on for an hour-plus run. If the local session dies mid-poll
    (laptop closed, network drop), nothing was watching the remote side
    at all before this — the instance would sit there, finished or not,
    burning money until someone noticed and terminated it manually. This
    races harmlessly against the local script's own explicit terminate
    call in the common case (whichever fires first wins; a shutdown
    command on an already-terminated instance is simply a no-op), and
    the `max_hours` absolute cap also catches the separate case where the
    remote job itself hangs and never writes its output file at all.
    """
    checks = " && ".join(f"test -f {f}" for f in output_files)
    watchdog_script = (
        f"deadline=$(( $(date +%s) + {max_hours}*3600 )); "
        f"while ! ({checks}) && [ $(date +%s) -lt $deadline ]; do sleep 30; done; "
        f"sleep {grace_minutes * 60}; "
        f"sudo shutdown -h now"
    )
    ssh_fn(
        f"cd {remote_root} && "
        f"(nohup bash -c '{watchdog_script}' > watchdog.log 2>&1 < /dev/null &) && echo watchdog started"
    )


def run_self_healing(
    ec2,
    ami_id: str,
    instance_type: str,
    key_name: str,
    sg_id: str,
    run_fn: Callable[[object, str], None],
    max_attempts: int,
    retry_delay_s: int,
    keep_instance: bool,
    region: str,
    name_prefix: str = "chess-annotate",
    spot: bool = True,
) -> None:
    """The hardened launch/run/cleanup retry loop, shared by every
    cloud/remote_*_aws.py script. `run_fn(ec2, ip)` does the actual remote
    work (rsync, ssh setup, poll-until-done) and should raise SystemExit on
    a detected reclaim — any other exception is treated identically (see
    the two `except` blocks below), so run_fn doesn't need to distinguish
    failure types itself.

    Three exception-handling gaps here have each been found the hard way in
    production (see PROJECT_LOG.md "Spot resilience round 3/4") — a launch
    failure that isn't a ClientError (a local network blip raises
    botocore's EndpointConnectionError, not a ClientError subclass), a
    setup/run failure that isn't the SystemExit run_fn raises for a
    detected reclaim (a flaky apt-get/pip install raises
    subprocess.CalledProcessError), and a cleanup failure in the `finally`
    block. All three are caught broadly now, on the single underlying
    principle: once a run_fn is written to checkpoint its work to disk
    (true for both annotate.py and maia_annotate.py), ANY failure after
    "job started" is safe to just retry — enumerating specific exception
    types is a trap, not a feature, for a script whose whole point is
    surviving unattended overnight."""
    for attempt in range(1, max_attempts + 1):
        market_label = "Spot" if spot else "on-demand"
        print(f"Launching {market_label} {instance_type} in {region} (AMI {ami_id})... [attempt {attempt}/{max_attempts}]")
        try:
            instance = launch_spot_instance(ec2, ami_id, instance_type, key_name, sg_id, name_prefix, spot=spot)
        except Exception as e:
            code = e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__
            print(f"  launch failed: {code} ({e}) — retrying in {retry_delay_s}s")
            if attempt == max_attempts:
                raise
            time.sleep(retry_delay_s)
            continue

        instance_id = instance["InstanceId"]
        try:
            ip = wait_for_running_and_ssh(ec2, instance_id)
            run_fn(ec2, ip)
            break  # completed successfully
        except (SystemExit, Exception) as e:
            if not isinstance(e, SystemExit):
                print(f"  setup/run failed ({type(e).__name__}: {e}) — treating as transient, retrying")
                # subprocess.CalledProcessError carries the remote command's
                # actual stdout/stderr (captured via capture_output=True in
                # every ssh() helper) — the str(e) above only ever shows the
                # command and exit code, silently discarding the one thing
                # that actually explains WHY. Found this gap by chasing a
                # generic "exit status 255" through several blind retries
                # with no way to tell a network blip from a real remote
                # Python traceback. Print both if present, every future
                # failure of this kind is diagnosable in one shot instead.
                stdout = getattr(e, "stdout", None)
                stderr = getattr(e, "stderr", None)
                if stdout:
                    print(f"  --- remote stdout ---\n{stdout}")
                if stderr:
                    print(f"  --- remote stderr ---\n{stderr}")
            print(f"  auto-recovering: relaunching (attempt {attempt + 1}/{max_attempts} if needed)...")
            if attempt == max_attempts:
                print(f"Hit max_attempts={max_attempts} — giving up. Re-run this command manually to keep trying; checkpoints are safe.")
                raise
        finally:
            if keep_instance:
                print(f"--keep-instance passed: leaving {instance_id} running. Remember to terminate it manually!")
            else:
                try:
                    ec2.terminate_instances(InstanceIds=[instance_id])
                    print(f"Terminated {instance_id} — billing stopped.")
                except Exception as term_err:
                    print(f"  couldn't confirm termination of {instance_id} ({type(term_err).__name__}: {term_err}) — check the AWS console if this recurs")
