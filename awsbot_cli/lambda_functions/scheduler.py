import boto3

from ..utils.logger import get_logger
from ..utils.scheduler import apply_action, discover_resources

logger = get_logger(__name__)


def scrub_for_log(value):
    """Flatten CR/LF so event-supplied values can't forge log lines (CWE-117)."""
    return str(value).replace("\n", " ").replace("\r", " ")


def handler(event, context):
    event = event or {}
    schedule_name = event["schedule"]
    action = event["action"]

    ec2 = boto3.client("ec2")
    asg = boto3.client("autoscaling")
    eb = boto3.client("elasticbeanstalk")
    rds = boto3.client("rds")

    logger.info(
        "--- Schedule: %s | Action: %s ---",
        scrub_for_log(schedule_name),
        scrub_for_log(action),
    )

    resources = discover_resources(ec2, asg, schedule_name, rds_client=rds)
    results = apply_action(
        ec2, asg, action, resources, dry_run=False, eb_client=eb, rds_client=rds
    )

    return {
        "statusCode": 200,
        "body": f"{action} complete for schedule '{schedule_name}'.",
        "details": {"results": results},
    }
