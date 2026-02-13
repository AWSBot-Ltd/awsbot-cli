import os

import boto3
from botocore.exceptions import ClientError

from ..utils.logger import get_logger

logger = get_logger(__name__)


def handler(event, context):
    ec2 = boto3.client("ec2")
    event = event or {}

    # 1. Configuration
    target_tag_key = event.get("target_tag") or os.environ.get("TARGET_TAG", "")
    target_env = event.get("environment") or os.environ.get("ENVIRONMENT", "dev")

    if "dry_run" in event:
        is_dry_run = event["dry_run"]
    else:
        is_dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"

    mode_str = "DRY RUN" if is_dry_run else "LIVE DELETE"
    logger.info(f"--- Execution Mode: {mode_str} | Env: {target_env} ---")

    # 2. Map AMIs to Instance Details (Store as dicts for flexibility)
    ami_to_instances = {}
    instance_paginator = ec2.get_paginator("describe_instances")

    for page in instance_paginator.paginate():
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                ami_id = instance["ImageId"]
                instance_id = instance["InstanceId"]

                # Get Instance Name
                tags = instance.get("Tags", [])
                inst_name = next(
                    (t["Value"] for t in tags if t["Key"] == "Name"), "No Name"
                )

                # Store structured data
                ami_to_instances.setdefault(ami_id, []).append(
                    {"id": instance_id, "name": inst_name}
                )

    # 3. Find target AMIs
    image_paginator = ec2.get_paginator("describe_images")
    image_iterator = image_paginator.paginate(
        Owners=["self"],
        Filters=[
            {"Name": "tag:Name", "Values": [target_tag_key]},
            {"Name": "tag:Environment", "Values": [target_env]},
        ],
    )

    cleanup_results = []  # Table 1: Actionable items
    in_use_results = []  # Table 2: Skipped/In-Use items
    deregistered_count = 0

    # 4. Process AMIs
    for page in image_iterator:
        for ami in page["Images"]:
            ami_id = ami["ImageId"]
            creation_date = ami.get("CreationDate", "N/A")

            # Get AMI Name
            tags = ami.get("Tags", [])
            ami_name = next((t["Value"] for t in tags if t["Key"] == "Name"), "N/A")

            # Check usage
            connected_instances = ami_to_instances.get(ami_id, [])

            if not connected_instances:
                # --- ACTION: DELETE ---
                status = "Deregistered"
                if is_dry_run:
                    status = "Would Deregister"
                    deregistered_count += 1
                else:
                    try:
                        logger.info(f"Deregistering AMI: {ami_id} ({ami_name})")
                        ec2.deregister_image(ImageId=ami_id)
                        deregistered_count += 1
                        status = "Deregistered"

                        for device in ami.get("BlockDeviceMappings", []):
                            if "Ebs" in device:
                                snap_id = device["Ebs"]["SnapshotId"]
                                ec2.delete_snapshot(SnapshotId=snap_id)

                    except ClientError as e:
                        logger.error(f"Error processing {ami_id}: {e}")
                        status = f"Error: {e}"

                # Add to Cleanup Table
                cleanup_results.append(
                    {
                        "AMI ID": ami_id,
                        "AMI Name": ami_name,
                        "Status": status,
                        "Created": creation_date,
                    }
                )

            else:
                # --- ACTION: SKIP (Populate In-Use Table) ---
                # We flatten the list: 1 row per instance using the AMI
                for inst in connected_instances:
                    in_use_results.append(
                        {
                            "AMI ID": ami_id,
                            "AMI Name": ami_name,
                            "Instance ID": inst["id"],
                            "Instance Name": inst["name"],
                            "Environment": target_env,
                        }
                    )

    return {
        "statusCode": 200,
        "body": f"{mode_str} complete. Identified {deregistered_count} unused AMIs.",
        "details": {"cleanup": cleanup_results, "in_use": in_use_results},
    }


if __name__ == "__main__":
    handler(None, None)
