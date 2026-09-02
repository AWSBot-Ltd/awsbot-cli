from datetime import datetime
from typing import Literal

from botocore.exceptions import ClientError

from .logger import get_logger

logger = get_logger(__name__)

SCHEDULE_TAG_KEY = "Schedule"
EB_ENV_TAG_KEY = "elasticbeanstalk:environment-name"

_DAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_DAY_ALIASES = {
    "weekdays": _DAY_CODES[:5],
    "weekends": _DAY_CODES[5:],
    "all": _DAY_CODES,
}


def resolve_days(days: str) -> list[str]:
    """Expand a days spec ('weekdays'/'weekends'/'all'/'MON,WED') into day codes."""
    alias = _DAY_ALIASES.get(days.strip().lower())
    if alias:
        return alias
    return [d.strip().upper() for d in days.split(",")]


def determine_action(schedule_cfg: dict, at: datetime) -> Literal["start", "stop"]:
    """
    Pure function: given a schedule config ({start_time, stop_time, days}, all
    UTC HH:MM) and a UTC datetime, decide whether that time falls in the
    "should be on" window.

    ponytail: UTC-only, same-day window only (stop_time must be > start_time).
    Upgrade path: EventBridge Scheduler w/ ScheduleExpressionTimezone for
    local-tz support; add an overnight branch if a stop_time < start_time
    schedule is ever needed.
    """
    day_code = _DAY_CODES[at.weekday()]
    if day_code not in resolve_days(schedule_cfg["days"]):
        return "stop"

    now_hm = at.strftime("%H:%M")
    in_window = schedule_cfg["start_time"] <= now_hm < schedule_cfg["stop_time"]
    return "start" if in_window else "stop"


def _tag_dict(tags: list) -> dict:
    return {t["Key"]: t["Value"] for t in tags or []}


def discover_resources(
    ec2_client, asg_client, schedule_name: str, rds_client=None
) -> dict:
    """
    Find EC2 instances, ASGs, and (if rds_client given) RDS DB instances
    tagged Schedule=<schedule_name>. Nothing without that tag is ever
    returned/touched — that tag is the sole opt-in/safety mechanism for
    this feature.
    """
    instances = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": f"tag:{SCHEDULE_TAG_KEY}", "Values": [schedule_name]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if inst["State"]["Name"] in ("terminated",):
                    continue
                instances.append(
                    {
                        "InstanceId": inst["InstanceId"],
                        "State": inst["State"]["Name"],
                        "Tags": _tag_dict(inst.get("Tags")),
                    }
                )

    asgs = []
    asg_paginator = asg_client.get_paginator("describe_auto_scaling_groups")
    for page in asg_paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            tags = _tag_dict(asg.get("Tags"))
            if tags.get(SCHEDULE_TAG_KEY) != schedule_name:
                continue
            asgs.append(
                {
                    "AutoScalingGroupName": asg["AutoScalingGroupName"],
                    "InstanceIds": [i["InstanceId"] for i in asg.get("Instances", [])],
                    "Tags": tags,
                }
            )

    db_instances = []
    if rds_client is not None:
        db_paginator = rds_client.get_paginator("describe_db_instances")
        for page in db_paginator.paginate():
            for db in page["DBInstances"]:
                tags = _tag_dict(db.get("TagList"))
                if tags.get(SCHEDULE_TAG_KEY) != schedule_name:
                    continue
                db_instances.append(
                    {
                        "DBInstanceIdentifier": db["DBInstanceIdentifier"],
                        "Status": db["DBInstanceStatus"],
                        "Tags": tags,
                    }
                )

    return {"instances": instances, "asgs": asgs, "db_instances": db_instances}


_SUSPEND_PROCESSES = [
    "Launch",
    "Terminate",
    "HealthCheck",
    "ReplaceUnhealthy",
    "AZRebalance",
]


def _eb_environment_info(eb_client, env_names: list) -> dict:
    """
    Return {environment_name: {"Application": str, "Status": str}} for the
    given Elastic Beanstalk environment names, via one batched describe call.
    Returns {} (fail open) if eb_client is None or the call errors -- a
    permissions gap here shouldn't block scheduling, only the EB-aware
    safety check that depends on it.
    """
    unique = sorted({n for n in env_names if n})
    if not unique or eb_client is None:
        return {}
    try:
        resp = eb_client.describe_environments(
            EnvironmentNames=unique, IncludeDeleted=False
        )
    except ClientError as e:
        logger.warning(f"Could not look up Elastic Beanstalk environments: {e}")
        return {}
    return {
        env["EnvironmentName"]: {
            "Application": env.get("ApplicationName", "-"),
            "Status": env.get("Status", "Unknown"),
        }
        for env in resp.get("Environments", [])
    }


def apply_action(
    ec2_client,
    asg_client,
    action: Literal["start", "stop"],
    resources: dict,
    dry_run: bool,
    eb_client=None,
    rds_client=None,
) -> list[dict]:
    """
    Start or stop the discovered instances/ASGs/DB instances. Every resource
    is handled independently so one failure doesn't abort the batch.

    If eb_client is given, an ASG tagged with the Elastic Beanstalk
    environment-name tag is skipped (not started/stopped) while that
    environment isn't in "Ready" status -- EB deploys drive the ASG directly
    (launching/replacing instances), and suspending processes or stopping
    instances mid-deploy can stall or break the deployment.

    ponytail: RDS Aurora clusters aren't supported (they must be started/
    stopped via start_db_cluster/stop_db_cluster on the cluster identifier,
    not per-member start_db_instance/stop_db_instance) -- add a "db_clusters"
    branch if Aurora scheduling is ever needed. Also note: AWS auto-restarts
    an RDS instance that's been stopped for 7 days; this scheduler doesn't
    fight that.

    ponytail: on `start`, DB instances are started (and waited on to reach
    "available") before the EC2/ASG app tier -- RDS takes minutes to come up
    where EC2 takes seconds, so starting them together races the app against
    its own database on every boot. The wait is bounded and fails open: if a
    DB isn't ready in time we log a warning and start the app tier anyway,
    rather than blocking the schedule indefinitely. On `stop` the order is
    unchanged (app tier down first, DB last).
    """
    rows = []

    db_instances = resources.get("db_instances", [])
    if action == "start":
        rows.extend(_apply_db_action(rds_client, action, db_instances, dry_run))
        _wait_for_db_instances_available(rds_client, db_instances, dry_run)

    eb_env_names = [
        asg.get("Tags", {}).get(EB_ENV_TAG_KEY) for asg in resources.get("asgs", [])
    ]
    eb_info = _eb_environment_info(eb_client, eb_env_names)

    for asg in resources.get("asgs", []):
        name = asg["AutoScalingGroupName"]
        env_name = asg.get("Tags", {}).get(EB_ENV_TAG_KEY)
        env_status = eb_info.get(env_name, {}).get("Status") if env_name else None
        if env_name and env_status is not None and env_status != "Ready":
            status = f"Skipped: EB environment '{env_name}' is {env_status}, not Ready"
            rows.append(
                {"resource_id": name, "type": "asg", "action": action, "status": status}
            )
            continue
        try:
            if dry_run:
                status = f"Would {action}"
            elif action == "stop":
                asg_client.suspend_processes(
                    AutoScalingGroupName=name, ScalingProcesses=_SUSPEND_PROCESSES
                )
                if asg["InstanceIds"]:
                    ec2_client.stop_instances(InstanceIds=asg["InstanceIds"])
                status = "Stopped"
            else:
                if asg["InstanceIds"]:
                    ec2_client.start_instances(InstanceIds=asg["InstanceIds"])
                    ec2_client.get_waiter("instance_running").wait(
                        InstanceIds=asg["InstanceIds"]
                    )
                asg_client.resume_processes(AutoScalingGroupName=name)
                status = "Started"
        except ClientError as e:
            logger.error(f"Error {action}ing ASG {name}: {e}")
            status = f"Error: {e}"

        rows.append(
            {"resource_id": name, "type": "asg", "action": action, "status": status}
        )

    for inst in resources.get("instances", []):
        instance_id = inst["InstanceId"]
        try:
            if dry_run:
                status = f"Would {action}"
            elif action == "stop":
                ec2_client.stop_instances(InstanceIds=[instance_id])
                status = "Stopped"
            else:
                ec2_client.start_instances(InstanceIds=[instance_id])
                status = "Started"
        except ClientError as e:
            logger.error(f"Error {action}ing instance {instance_id}: {e}")
            status = f"Error: {e}"

        rows.append(
            {
                "resource_id": instance_id,
                "type": "instance",
                "action": action,
                "status": status,
            }
        )

    if action != "start":
        rows.extend(_apply_db_action(rds_client, action, db_instances, dry_run))

    return rows


def _apply_db_action(
    rds_client,
    action: Literal["start", "stop"],
    db_instances: list[dict],
    dry_run: bool,
) -> list[dict]:
    rows = []
    for db in db_instances:
        db_id = db["DBInstanceIdentifier"]
        try:
            if dry_run:
                status = f"Would {action}"
            elif action == "stop":
                rds_client.stop_db_instance(DBInstanceIdentifier=db_id)
                status = "Stopped"
            else:
                rds_client.start_db_instance(DBInstanceIdentifier=db_id)
                status = "Started"
        except ClientError as e:
            logger.error(f"Error {action}ing DB instance {db_id}: {e}")
            status = f"Error: {e}"

        rows.append(
            {
                "resource_id": db_id,
                "type": "db_instance",
                "action": action,
                "status": status,
            }
        )
    return rows


def _wait_for_db_instances_available(
    rds_client, db_instances: list[dict], dry_run: bool
) -> None:
    """
    Block (bounded, ~8 min per instance) until each DB instance reports
    "available", so the app tier isn't started against a database that
    isn't accepting connections yet. Fails open: a timeout is logged and
    swallowed rather than raised, so a slow/stuck DB doesn't stop the app
    tier from starting at all.

    ponytail: budget is sized against the Lambda's 900s (15 min, the AWS
    max) timeout -- ~8 min for the DB, leaving the remainder for the
    EC2/ASG starts that follow. Bump both together if either changes.
    """
    if dry_run:
        return
    waiter = rds_client.get_waiter("db_instance_available")
    for db in db_instances:
        db_id = db["DBInstanceIdentifier"]
        try:
            waiter.wait(
                DBInstanceIdentifier=db_id,
                WaiterConfig={"Delay": 20, "MaxAttempts": 24},
            )
        except Exception as e:
            logger.warning(
                f"DB instance {db_id} did not become available within the wait budget: {e}"
            )


def list_all(ec2_client, asg_client, eb_client=None, rds_client=None) -> dict:
    """
    List every EC2 instance, ASG, and (if rds_client given) RDS DB instance
    (not just scheduled ones), each carrying its Schedule tag value (or None
    if untagged) -- lets the operator see at a glance what's covered by
    scheduling and what still needs tagging.

    If eb_client is given, resources tagged as Elastic Beanstalk-managed get
    their Application/Environment names resolved (EB names its ASGs/instances
    with opaque IDs, so the raw resource name alone isn't useful for finding
    which app/env it belongs to).
    """
    instances = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if inst["State"]["Name"] == "terminated":
                    continue
                tags = _tag_dict(inst.get("Tags"))
                instances.append(
                    {
                        "InstanceId": inst["InstanceId"],
                        "Name": tags.get("Name", "-"),
                        "State": inst["State"]["Name"],
                        "Schedule": tags.get(SCHEDULE_TAG_KEY),
                        "EBEnvironment": tags.get(EB_ENV_TAG_KEY),
                    }
                )

    asgs = []
    asg_paginator = asg_client.get_paginator("describe_auto_scaling_groups")
    for page in asg_paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            tags = _tag_dict(asg.get("Tags"))
            asgs.append(
                {
                    "AutoScalingGroupName": asg["AutoScalingGroupName"],
                    "Schedule": tags.get(SCHEDULE_TAG_KEY),
                    "EBEnvironment": tags.get(EB_ENV_TAG_KEY),
                }
            )

    env_names = [r["EBEnvironment"] for r in instances + asgs if r["EBEnvironment"]]
    eb_info = _eb_environment_info(eb_client, env_names)

    for r in instances + asgs:
        env_name = r.pop("EBEnvironment")
        info = eb_info.get(env_name, {}) if env_name else {}
        r["Application"] = info.get("Application", "-")
        r["Environment"] = env_name or "-"

    db_instances = []
    if rds_client is not None:
        db_paginator = rds_client.get_paginator("describe_db_instances")
        for page in db_paginator.paginate():
            for db in page["DBInstances"]:
                tags = _tag_dict(db.get("TagList"))
                db_instances.append(
                    {
                        "DBInstanceIdentifier": db["DBInstanceIdentifier"],
                        "Status": db["DBInstanceStatus"],
                        "Schedule": tags.get(SCHEDULE_TAG_KEY),
                    }
                )

    return {"instances": instances, "asgs": asgs, "db_instances": db_instances}


def tag_schedule(
    ec2_client,
    asg_client,
    resource_type: Literal["instance", "asg", "db_instance"],
    resource_ids: list[str],
    schedule_name: str,
    rds_client=None,
) -> None:
    """Opt EC2 instances, an ASG, or RDS DB instances into a named schedule."""
    if resource_type == "instance":
        ec2_client.create_tags(
            Resources=resource_ids,
            Tags=[{"Key": SCHEDULE_TAG_KEY, "Value": schedule_name}],
        )
    elif resource_type == "asg":
        for asg_name in resource_ids:
            asg_client.create_or_update_tags(
                Tags=[
                    {
                        "ResourceId": asg_name,
                        "ResourceType": "auto-scaling-group",
                        "Key": SCHEDULE_TAG_KEY,
                        "Value": schedule_name,
                        "PropagateAtLaunch": False,
                    }
                ]
            )
    else:
        for db_id in resource_ids:
            resp = rds_client.describe_db_instances(DBInstanceIdentifier=db_id)
            arn = resp["DBInstances"][0]["DBInstanceArn"]
            rds_client.add_tags_to_resource(
                ResourceName=arn,
                Tags=[{"Key": SCHEDULE_TAG_KEY, "Value": schedule_name}],
            )
