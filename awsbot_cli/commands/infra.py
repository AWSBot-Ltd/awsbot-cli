import random
import sys
import time
from datetime import datetime
from typing import Optional

import boto3
import requests
import typer

from awsbot_cli.lambda_functions import cleanup_amis
from awsbot_cli.utils.logger import get_logger, print_formatted_output
from awsbot_cli.utils.ssm_handler import SSMConnector

logger = get_logger(__name__)
app = typer.Typer(help="Infrastructure Management")


def find_target_instance(
    project: str, env: str, profile: str = None
) -> tuple[str, str]:
    """
    Finds a running instance by looking up the Auto Scaling Group first.
    Returns: (Instance ID, Private IP)
    """
    session = boto3.Session(profile_name=profile)
    asg_client = session.client("autoscaling")
    ec2_client = session.client("ec2")

    logger.info(f"🔍 Searching for ASGs with Project='{project}' and Env='{env}'...")

    # 1. Find ASGs that match the tags
    # ASG API filtering is limited, so we iterate (usually fast enough)
    matching_asgs = []
    paginator = asg_client.get_paginator("describe_auto_scaling_groups")

    for page in paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            tags = {t["Key"]: t["Value"] for t in asg.get("Tags", [])}
            if tags.get("Project") == project and tags.get("Environment") == env:
                matching_asgs.append(asg)

    if not matching_asgs:
        logger.error(f"❌ No ASGs found with tags Project={project}, Env={env}")
        sys.exit(1)

    logger.info(
        f"   Found {len(matching_asgs)} matching ASG(s). Checking for instances..."
    )

    # 2. Extract InService instances from the found ASGs
    candidate_ids = []
    for asg in matching_asgs:
        for inst in asg.get("Instances", []):
            if inst["LifecycleState"] == "InService":
                candidate_ids.append(inst["InstanceId"])

    if not candidate_ids:
        logger.error("❌ Matching ASG found, but it has no 'InService' instances.")
        sys.exit(1)

    # 3. Pick a random target
    target_id = random.choice(candidate_ids)

    # 4. Resolve Private IP for context (and validity check)
    try:
        resp = ec2_client.describe_instances(InstanceIds=[target_id])
        if not resp["Reservations"]:
            raise ValueError("Instance not found in EC2.")

        inst_data = resp["Reservations"][0]["Instances"][0]
        target_ip = inst_data.get("PrivateIpAddress", "Unknown")

    except Exception as e:
        logger.warning(f"⚠️ Could not resolve details for {target_id}: {e}")
        target_ip = "Unknown"

    logger.info(f"✅ Selected target: {target_id} ({target_ip})")
    return target_id, target_ip


@app.command("connect")
def connect(
    instance_id: Optional[str] = typer.Argument(
        None, help="Instance ID (Optional if using flags)"
    ),
    project: str = typer.Option(None, help="Project tag to search for"),
    env: str = typer.Option(None, help="Environment tag to search for"),
    profile: str = typer.Option(None, help="AWS Profile"),
):
    """
    Connect to an instance via SSM.
    Provide either an Instance ID directly OR use --project and --env to find one.
    """

    # 1. Resolve the Target
    target_id = instance_id

    if not target_id:
        if project and env:
            # We unpack the tuple here, ignoring the IP since SSM only needs the ID
            target_id, _ = find_target_instance(project, env, profile)
        else:
            typer.echo(
                "❌ Error: You must provide either an Instance ID OR both --project and --env."
            )
            raise typer.Exit(code=1)

    # 2. Connect
    print(f"🚀 Connecting to {target_id}...")
    connector = SSMConnector(profile=profile)
    connector.start_interactive_session(target_id)


@app.command("clean-amis")
def clean_amis(
    env: str = typer.Option(None, help="Target environment"),
    dry_run: bool = typer.Option(False, help="Simulate actions"),
):
    """Find and deregister unused AMIs."""
    print("Starting AMI Cleanup Workflow...")

    # Call the lambda logic
    result = cleanup_amis.handler({"environment": env, "dry_run": dry_run}, None)

    # Process results (Logic moved from engine.py)
    data = result.get("details") or {}
    cleanup_list = data.get("cleanup", []) if isinstance(data, dict) else data
    in_use_list = data.get("in_use", []) if isinstance(data, dict) else []

    if cleanup_list:
        print("\n=== AMIS TO CLEAN UP ===")
        print_formatted_output(
            cleanup_list, headers=["AMI ID", "Status", "Created", "AMI Name"]
        )

    if in_use_list:
        print("\n=== SKIPPED AMIS (CURRENTLY IN USE) ===")
        print_formatted_output(
            in_use_list, headers=["AMI ID", "Instance ID", "Instance Name", "AMI Name"]
        )


# ---------------------------------------------------------
# HELPER: Find ASG by Tags
# ---------------------------------------------------------
def get_asg_name(project: str, env: str, profile: str = None) -> str:
    """Finds the ASG name based on Project and Environment tags."""
    session = boto3.Session(profile_name=profile)
    asg_client = session.client("autoscaling")

    # We have to iterate because ASG describe_auto_scaling_groups doesn't support server-side tag filtering
    paginator = asg_client.get_paginator("describe_auto_scaling_groups")

    for page in paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            tags = {t["Key"]: t["Value"] for t in asg.get("Tags", [])}
            if tags.get("Project") == project and tags.get("Environment") == env:
                return asg["AutoScalingGroupName"]

    raise ValueError(f"No ASG found for Project='{project}' in Env='{env}'")


# ---------------------------------------------------------
# COMMAND: Instance Refresh
# ---------------------------------------------------------
@app.command("refresh")
def refresh_asg(
    project: str = typer.Option(..., help="Project tag (e.g., consumer)"),
    env: str = typer.Option(..., help="Environment tag (e.g., dev)"),
    profile: str = typer.Option(None, help="AWS Profile"),
    min_healthy: int = typer.Option(
        100, help="Min % of instances that must remain healthy (Default: 90)"
    ),
    max_healthy: int = typer.Option(
        110, help="Max % of instances that must remain healthy (Default: 90)"
    ),
    warmup: int = typer.Option(
        300, help="Seconds to wait for a new instance to be ready (Default: 300)"
    ),
    bake_time: int = typer.Option(
        0, help="Seconds to wait between checkpoints (Bake time). Default 0."
    ),
    checkpoint_percentages: str = typer.Option(
        None, help="Comma-separated list of percentages to pause at (e.g. '10,50')."
    ),
):
    """
    Trigger a safe Instance Refresh for a specific Project/Env.
    Allows controlling bake times and healthy percentages to prevent outages.
    """
    session = boto3.Session(profile_name=profile)
    client = session.client("autoscaling")

    # 1. Resolve ASG Name
    try:
        asg_name = get_asg_name(project, env, profile)
        print(f"🎯 Found ASG: {asg_name}")
    except ValueError as e:
        print(f"❌ {e}")
        raise typer.Exit(1)

    # 2. Build Preferences
    # 'Bake Time' is implemented via CheckpointDelay
    preferences = {
        "MinHealthyPercentage": min_healthy,
        "MaxHealthyPercentage": max_healthy,
        "InstanceWarmup": warmup,
        "SkipMatching": False,  # Force replacement even if launch template matches
    }

    if bake_time > 0:
        preferences["CheckpointDelay"] = bake_time

    if checkpoint_percentages:
        # Parse "10,50" into [10, 50]
        percents = [int(p.strip()) for p in checkpoint_percentages.split(",")]
        preferences["CheckpointPercentages"] = percents
        print(f"⏸️  Checkpoints enabled: Will pause for {bake_time}s at {percents}%")

    # 3. Start Refresh
    print(
        f"🚀 Starting Instance Refresh (MinHealthy: {min_healthy}%, Warmup: {warmup}s)..."
    )

    try:
        response = client.start_instance_refresh(
            AutoScalingGroupName=asg_name, Strategy="Rolling", Preferences=preferences
        )
        refresh_id = response["InstanceRefreshId"]
        print(f"✅ Refresh Started! ID: {refresh_id}")
    except client.exceptions.InstanceRefreshInProgressFault:
        print(
            "❌ A refresh is already in progress. Use AWS Console to cancel it if needed."
        )
        raise typer.Exit(1)
    except Exception as e:
        print(f"❌ Failed to start refresh: {e}")
        raise typer.Exit(1)

    # 4. Poll Loop
    try:
        while True:
            desc = client.describe_instance_refreshes(
                AutoScalingGroupName=asg_name, InstanceRefreshIds=[refresh_id]
            )
            data = desc["InstanceRefreshes"][0]
            status = data["Status"]
            percent = data.get("PercentageComplete", 0)
            status_reason = data.get("StatusReason", "")

            # Clear line and print status
            print(
                f"\r🔄 Status: {status} | Progress: {percent}% | {status_reason}",
                end="",
            )

            if status == "Successful":
                print("\n✅ Refresh Completed Successfully!")
                break

            if status in [
                "Failed",
                "Cancelled",
                "RollbackInProgress",
                "RollbackFailed",
            ]:
                print(f"\n❌ Refresh ended with status: {status}")
                # Optional: Describe events to see why
                raise typer.Exit(1)

            time.sleep(10)

    except KeyboardInterrupt:
        print("\n⚠️  Monitoring stopped (Refresh continues in background).")


@app.command("check-health")
def check_health(
    project: str = typer.Option(..., help="Project name"),
    env: str = typer.Option(..., help="Environment"),
    interval: int = typer.Option(5, help="Polling interval in seconds"),
    max_retries: int = typer.Option(
        12, help="Max retry attempts (ignored if --monitor is used)"
    ),
    profile: str = typer.Option(None, help="AWS Profile"),
    monitor: bool = typer.Option(
        False, help="Keep polling continuously to track stability during updates."
    ),
    duration: int = typer.Option(
        0, help="If monitoring, stop after this many seconds (0 = infinite)."
    ),
):
    """
    Fetch the service URL and poll its health.
    Use --monitor to watch traffic continuously during a deployment.
    """
    session = boto3.Session(profile_name=profile)
    cfn = session.client("cloudformation")

    stack_name = f"PlatformComputeStack-{env}"
    export_name = f"{project}-{env}-url"

    print(f"🔍 Fetching Service URL for Project: {project} in {env}...")

    try:
        response = cfn.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0]["Outputs"]

        service_url = None
        for out in outputs:
            if out.get("ExportName") == export_name:
                service_url = out["OutputValue"]
                break

        if not service_url:
            print(f"❌ Error: Export '{export_name}' not found in stack '{stack_name}'")
            raise typer.Exit(1)

    except Exception as e:
        print(f"❌ Error fetching stack outputs: {e}")
        raise typer.Exit(1)

    print(f"🎯 Target: {service_url}")

    if monitor:
        print("👀 Monitoring active. Press Ctrl+C to stop.")
    else:
        print(f"⏳ Polling until healthy (Max retries: {max_retries})...")

    current_url = service_url
    count = 1
    start_time = time.time()

    try:
        while True:
            # 1. Check Duration limit (if monitoring)
            if monitor and duration > 0:
                elapsed = time.time() - start_time
                if elapsed > duration:
                    print(f"\n🕒 Duration of {duration}s reached. Monitoring complete.")
                    raise typer.Exit(0)

            # 2. Check Retry limit (if NOT monitoring)
            if not monitor and count > max_retries:
                print(
                    f"\n❌ Timeout: Service did not become healthy after {max_retries * interval} seconds."
                )
                raise typer.Exit(1)

            # 3. Perform Request
            timestamp = datetime.now().strftime("%H:%M:%S")
            try:
                # verify=False is useful for internal dev envs with self-signed certs
                r = requests.get(current_url, timeout=5)
                status = r.status_code

                if status == 200:
                    if monitor:
                        print(f"[{timestamp}] ✅ Healthy (200 OK)")
                    else:
                        print("\n✅ Success! Service is Healthy (HTTP 200)")
                        raise typer.Exit(0)

                elif status in [301, 308]:
                    # Handle redirects (add trailing slash logic)
                    print(f"[{timestamp}] ➡️  Received {status}. Adjusting URL...")
                    if not current_url.endswith("/"):
                        current_url += "/"
                    # Don't sleep, retry immediately with new URL
                    continue

                else:
                    print(f"[{timestamp}] ⚠️  Status: {status}")

            except requests.exceptions.RequestException as e:
                print(f"[{timestamp}] ❌ Connection failed: {e}")

            time.sleep(interval)
            count += 1

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
        raise typer.Exit(0)
