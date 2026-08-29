import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from awsbot_cli.utils.config import get_schedule, list_schedules, save_schedule
from awsbot_cli.utils.scheduler import (
    apply_action,
    determine_action,
    discover_resources,
    list_all,
    resolve_days,
    tag_schedule,
)

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "cloudformation"
    / "scheduler"
    / "template.yaml"
)
SRC_DIR = TEMPLATE_PATH.parent / "src"

app = typer.Typer(help="Schedule EC2/ASG instances to stop/start on a recurring window")
console = Console()


@app.command("set-schedule")
def set_schedule(
    name: str = typer.Argument(..., help="Schedule name, e.g. office-hours"),
    start: str = typer.Option(..., "--start", help="Start time, UTC HH:MM"),
    stop: str = typer.Option(..., "--stop", help="Stop time, UTC HH:MM"),
    days: str = typer.Option(
        "weekdays", "--days", help="weekdays | weekends | all | MON,WED,..."
    ),
):
    """Create or overwrite a named schedule."""
    save_schedule(name, start, stop, days)
    typer.secho(
        f"Saved schedule '{name}': {start}-{stop} UTC, {days}", fg=typer.colors.GREEN
    )


@app.command("list-schedules")
def list_schedules_cmd():
    """List all saved schedules."""
    schedules = list_schedules()
    if not schedules:
        typer.echo("No schedules configured.")
        return

    table = Table(title="Schedules")
    table.add_column("Name")
    table.add_column("Start (UTC)")
    table.add_column("Stop (UTC)")
    table.add_column("Days")
    for name, cfg in schedules.items():
        table.add_row(name, cfg["start_time"], cfg["stop_time"], cfg["days"])
    console.print(table)


@app.command("list-instances")
def list_instances_cmd(
    schedule: Optional[str] = typer.Option(
        None, "--schedule", help="Only show resources tagged with this schedule"
    ),
    untagged_only: bool = typer.Option(
        False, "--untagged-only", help="Only show resources with no Schedule tag"
    ),
    profile: str = typer.Option(None, "--profile", help="AWS Profile"),
    region: str = typer.Option(None, "--region", help="AWS Region"),
):
    """List EC2 instances, ASGs, and RDS DB instances with their Schedule tag
    status (tagged/untagged). Elastic Beanstalk-managed resources show their
    Application/Environment name instead of the opaque ASG/instance ID alone."""
    session = boto3.Session(profile_name=profile, region_name=region)
    ec2_client = session.client("ec2")
    asg_client = session.client("autoscaling")
    eb_client = session.client("elasticbeanstalk")
    rds_client = session.client("rds")

    try:
        resources = list_all(
            ec2_client, asg_client, eb_client=eb_client, rds_client=rds_client
        )
    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    def _keep(sched_value):
        if untagged_only:
            return sched_value is None
        if schedule:
            return sched_value == schedule
        return True

    instances = [i for i in resources["instances"] if _keep(i["Schedule"])]
    asgs = [a for a in resources["asgs"] if _keep(a["Schedule"])]
    db_instances = [d for d in resources["db_instances"] if _keep(d["Schedule"])]

    if not instances and not asgs and not db_instances:
        typer.echo("No matching resources found.")
        return

    if instances:
        table = Table(title="EC2 Instances")
        table.add_column("Instance ID")
        table.add_column("Name")
        table.add_column("State")
        table.add_column("Schedule")
        table.add_column("EB Application")
        table.add_column("EB Environment")
        for i in instances:
            schedule_cell = i["Schedule"] or "[red]untagged[/red]"
            table.add_row(
                i["InstanceId"],
                i["Name"],
                i["State"],
                schedule_cell,
                i["Application"],
                i["Environment"],
            )
        console.print(table)

    if asgs:
        table = Table(title="Auto Scaling Groups")
        table.add_column("ASG Name")
        table.add_column("Schedule")
        table.add_column("EB Application")
        table.add_column("EB Environment")
        for a in asgs:
            schedule_cell = a["Schedule"] or "[red]untagged[/red]"
            table.add_row(
                a["AutoScalingGroupName"],
                schedule_cell,
                a["Application"],
                a["Environment"],
            )
        console.print(table)

    if db_instances:
        table = Table(title="RDS DB Instances")
        table.add_column("DB Instance ID")
        table.add_column("Status")
        table.add_column("Schedule")
        for d in db_instances:
            schedule_cell = d["Schedule"] or "[red]untagged[/red]"
            table.add_row(d["DBInstanceIdentifier"], d["Status"], schedule_cell)
        console.print(table)


@app.command("tag")
def tag(
    schedule: str = typer.Option(..., "--schedule", help="Schedule name to opt in to"),
    instance_id: Optional[List[str]] = typer.Option(
        None, "--instance-id", help="EC2 instance ID(s) to tag"
    ),
    asg_name: Optional[List[str]] = typer.Option(
        None, "--asg-name", help="Auto Scaling Group name(s) to tag"
    ),
    db_instance_id: Optional[List[str]] = typer.Option(
        None, "--db-instance-id", help="RDS DB instance identifier(s) to tag"
    ),
    profile: str = typer.Option(None, "--profile", help="AWS Profile"),
    region: str = typer.Option(None, "--region", help="AWS Region"),
):
    """Tag EC2 instances, ASGs, or RDS DB instances with Schedule=<name>, opting them into a schedule."""
    if not instance_id and not asg_name and not db_instance_id:
        typer.secho(
            "Error: provide at least one --instance-id, --asg-name, or --db-instance-id.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    session = boto3.Session(profile_name=profile, region_name=region)
    ec2_client = session.client("ec2")
    asg_client = session.client("autoscaling")
    rds_client = session.client("rds")

    try:
        if instance_id:
            tag_schedule(
                ec2_client, asg_client, "instance", list(instance_id), schedule
            )
            typer.secho(
                f"Tagged {len(instance_id)} instance(s).", fg=typer.colors.GREEN
            )
        if asg_name:
            tag_schedule(ec2_client, asg_client, "asg", list(asg_name), schedule)
            typer.secho(f"Tagged {len(asg_name)} ASG(s).", fg=typer.colors.GREEN)
        if db_instance_id:
            tag_schedule(
                ec2_client,
                asg_client,
                "db_instance",
                list(db_instance_id),
                schedule,
                rds_client=rds_client,
            )
            typer.secho(
                f"Tagged {len(db_instance_id)} DB instance(s).", fg=typer.colors.GREEN
            )
    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)


def _print_results(results: list[dict]):
    table = Table(title="Scheduler Run")
    table.add_column("Type")
    table.add_column("Resource")
    table.add_column("Action")
    table.add_column("Status")
    for row in results:
        table.add_row(row["type"], row["resource_id"], row["action"], row["status"])
    console.print(table)


@app.command("run")
def run(
    schedule: str = typer.Option(..., "--schedule", help="Schedule name"),
    action: Optional[str] = typer.Option(
        None, "--action", help="Force 'start' or 'stop' instead of computing it"
    ),
    at: Optional[str] = typer.Option(
        None,
        "--at",
        help="ISO8601 UTC timestamp to evaluate the schedule at (for testing)",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview actions without making changes (default)",
    ),
    profile: str = typer.Option(None, "--profile", help="AWS Profile"),
    region: str = typer.Option(None, "--region", help="AWS Region"),
):
    """Evaluate a schedule and start/stop its tagged resources. Dry-run by default."""
    if action is None:
        cfg = get_schedule(schedule)
        if not cfg:
            typer.secho(f"Error: unknown schedule '{schedule}'.", fg=typer.colors.RED)
            raise typer.Exit(1)
        at_dt = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
        action = determine_action(cfg, at_dt)

    session = boto3.Session(profile_name=profile, region_name=region)
    ec2_client = session.client("ec2")
    asg_client = session.client("autoscaling")
    eb_client = session.client("elasticbeanstalk")
    rds_client = session.client("rds")

    try:
        resources = discover_resources(
            ec2_client, asg_client, schedule, rds_client=rds_client
        )
    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    if (
        not resources["instances"]
        and not resources["asgs"]
        and not resources["db_instances"]
    ):
        typer.echo(f"No resources tagged Schedule={schedule}.")
        return

    mode = "DRY RUN" if dry_run else "APPLY"
    typer.echo(f"[{mode}] action={action}")

    results = apply_action(
        ec2_client,
        asg_client,
        action,
        resources,
        dry_run=dry_run,
        eb_client=eb_client,
        rds_client=rds_client,
    )
    _print_results(results)


def _cron_expr(hh_mm: str, days: str) -> str:
    """Build an EventBridge cron() expression from HH:MM UTC + a days spec."""
    hour, minute = hh_mm.split(":")
    day_list = ",".join(resolve_days(days))
    return f"cron({int(minute)} {int(hour)} ? * {day_list} *)"


def _stage_lambda_source() -> None:
    """
    Copy the canonical Lambda handler and its deps into the SAM CodeUri dir
    (src/), preserving their package nesting (awsbot_cli/lambda_functions/,
    awsbot_cli/utils/) so the handler's existing relative imports keep
    working unmodified -- no typer/rich/config/boto3-cfn code is included.
    """
    if SRC_DIR.exists():
        shutil.rmtree(SRC_DIR)

    pkg_root = Path(__file__).resolve().parent.parent  # awsbot_cli/
    files = {
        "awsbot_cli/__init__.py": pkg_root / "__init__.py",
        "awsbot_cli/lambda_functions/__init__.py": pkg_root
        / "lambda_functions"
        / "__init__.py",
        "awsbot_cli/lambda_functions/scheduler.py": pkg_root
        / "lambda_functions"
        / "scheduler.py",
        "awsbot_cli/utils/__init__.py": pkg_root / "utils" / "__init__.py",
        "awsbot_cli/utils/scheduler.py": pkg_root / "utils" / "scheduler.py",
        "awsbot_cli/utils/logger.py": pkg_root / "utils" / "logger.py",
    }
    for rel_path, src_file in files.items():
        dest = SRC_DIR / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)


def _sam_param_overrides(schedule: str, cfg: dict) -> str:
    """
    Build a single shlex-parseable --parameter-overrides string. sam-cli
    parses this as its own token stream, so quoting each value lets cron
    expressions (which contain spaces) survive as one parameter.
    """
    params = {
        "ScheduleName": schedule,
        "StartCron": _cron_expr(cfg["start_time"], cfg["days"]),
        "StopCron": _cron_expr(cfg["stop_time"], cfg["days"]),
    }
    return " ".join(f'{k}="{v}"' for k, v in params.items())


@app.command("deploy")
def deploy(
    schedule: str = typer.Option(..., "--schedule", help="Schedule name to deploy"),
    stack_name: Optional[str] = typer.Option(
        None, "--stack-name", help="CloudFormation stack name"
    ),
    profile: str = typer.Option(None, "--profile", help="AWS Profile"),
    region: str = typer.Option(None, "--region", help="AWS Region"),
):
    """Deploy (or update) the Lambda + EventBridge schedules via AWS SAM (`sam deploy`)."""
    cfg = get_schedule(schedule)
    if not cfg:
        typer.secho(
            f"Error: unknown schedule '{schedule}'. Run set-schedule first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    stack_name = stack_name or f"awsbot-scheduler-{schedule}"

    typer.echo("Staging Lambda source...")
    _stage_lambda_source()

    cmd = [
        "sam",
        "deploy",
        "--template-file",
        str(TEMPLATE_PATH),
        "--stack-name",
        stack_name,
        "--resolve-s3",
        "--capabilities",
        "CAPABILITY_IAM",
        "--no-confirm-changeset",
        "--no-fail-on-empty-changeset",
        "--parameter-overrides",
        _sam_param_overrides(schedule, cfg),
    ]
    if profile:
        cmd += ["--profile", profile]
    if region:
        cmd += ["--region", region]

    typer.echo(f"Deploying stack '{stack_name}' via SAM...")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        typer.secho(
            "Error: 'sam' CLI not found. Install the AWS SAM CLI first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        typer.secho(f"sam deploy failed (exit {e.returncode}).", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"Stack '{stack_name}' deployment complete.", fg=typer.colors.GREEN)

    function_name = f"awsbot-scheduler-{schedule}"
    runbook_name = f"awsbot-scheduler-{schedule}-override"
    typer.echo(
        f"\nManual start/stop for non-technical users: Systems Manager > Automation > "
        f"Execute automation > '{runbook_name}' (choose Action=start or stop).\n"
        f"To grant someone access, attach an IAM policy allowing "
        f"ssm:StartAutomationExecution/GetAutomationExecution/DescribeAutomationExecutions "
        f"on automation-definition/{runbook_name}:* and lambda:InvokeFunction on "
        f"function:{function_name} -- nothing else is needed."
    )
