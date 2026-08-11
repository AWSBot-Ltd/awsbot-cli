"""CloudWatch Logs CLI: list log groups/streams and read log events."""

import os
import re
from datetime import datetime, timedelta, timezone

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="List log groups, streams, read events, and manage retention")
console = Console()


def _default_region() -> str:
    """Return AWS region from env or session, falling back to us-east-1."""
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if region:
        return region
    try:
        session = boto3.Session()
        if session.region_name:
            return session.region_name
    except Exception:
        pass
    return "us-east-1"


def get_logs_client(region: str | None = None):
    """Return a boto3 CloudWatch Logs client for the given region."""
    if region is None:
        region = _default_region()
    return boto3.client("logs", region_name=region)


def _parse_since(since: str) -> int:
    """
    Parse a relative time string (e.g. '1h', '30m', '2d') into Unix timestamp in milliseconds.
    Returns start time (now - since).
    """
    match = re.match(r"^(\d+)(m|h|d)$", since.strip().lower())
    if not match:
        raise typer.BadParameter(
            "since must be like 30m, 1h, 2d (minutes, hours, days)"
        )
    value, unit = int(match.group(1)), match.group(2)
    now = datetime.now(timezone.utc)
    if unit == "m":
        start = now - timedelta(minutes=value)
    elif unit == "h":
        start = now - timedelta(hours=value)
    else:  # d
        start = now - timedelta(days=value)
    return int(start.timestamp() * 1000)


# Valid retention values (days) per AWS CloudWatch Logs API
ALLOWED_RETENTION_DAYS = {
    1,
    3,
    5,
    7,
    14,
    30,
    60,
    90,
    120,
    150,
    180,
    365,
    400,
    545,
    731,
    1096,
    1827,
    2192,
    2557,
    2922,
    3288,
    3653,
}


def _validate_retention_days(days: int) -> None:
    """Raise BadParameter if days is not an allowed CloudWatch Logs retention value."""
    if days not in ALLOWED_RETENTION_DAYS:
        raise typer.BadParameter(
            f"retention must be one of: {sorted(ALLOWED_RETENTION_DAYS)}"
        )


# --- list-groups ---


@app.command("list-groups")
def list_groups(
    prefix: str = typer.Option(None, "--prefix", "-p", help="Log group name prefix"),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max number of groups"),
):
    """List CloudWatch log groups with name and retention, optionally filtered by prefix."""
    client = get_logs_client(region)
    try:
        kwargs = {"limit": limit}
        if prefix:
            kwargs["logGroupNamePrefix"] = prefix
        response = client.describe_log_groups(**kwargs)
        groups = response.get("logGroups", [])
        if not groups:
            typer.echo("No log groups found.")
            return
        table = Table(title="Log groups")
        table.add_column("Name", style="cyan")
        table.add_column("Retention", style="dim")
        for g in groups:
            retention = g.get("retentionInDays")
            retention_str = (
                f"{retention} days" if retention is not None else "Never expire"
            )
            table.add_row(g.get("logGroupName", ""), retention_str)
        console.print(table)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)


# --- set-retention ---


@app.command("set-retention")
def set_retention(
    log_group: str = typer.Option(..., "--log-group", "-g", help="Log group name"),
    days: int | None = typer.Option(
        None,
        "--days",
        "-d",
        help="Retention in days; valid values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, etc. (required unless --remove)",
    ),
    remove: bool = typer.Option(
        False, "--remove", help="Clear retention so logs never expire"
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
):
    """Set or clear retention policy for a single log group."""
    if not remove and days is None:
        typer.secho("Either --days or --remove is required.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if remove and days is not None:
        typer.secho("Use either --days or --remove, not both.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not remove:
        _validate_retention_days(days)
    client = get_logs_client(region)
    try:
        if remove:
            client.delete_retention_policy(logGroupName=log_group)
            typer.echo(f"Retention removed for log group: {log_group}")
        else:
            client.put_retention_policy(logGroupName=log_group, retentionInDays=days)
            typer.echo(f"Retention set to {days} days for log group: {log_group}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)


# --- set-retention-all ---


@app.command("set-retention-all")
def set_retention_all(
    days: int | None = typer.Option(
        None,
        "--days",
        "-d",
        help="Retention in days; valid values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, etc. (required unless --remove)",
    ),
    prefix: str = typer.Option(None, "--prefix", "-p", help="Log group name prefix"),
    only_unset: bool = typer.Option(
        False, "--only-unset", help="Only update groups that have no retention set"
    ),
    remove: bool = typer.Option(
        False, "--remove", help="Clear retention on all matched groups"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Max number of groups to update (default: no limit)"
    ),
):
    """Set or clear retention policy for all log groups (optionally filtered by prefix)."""
    if not remove and days is None:
        typer.secho("Either --days or --remove is required.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if remove and days is not None:
        typer.secho("Use either --days or --remove, not both.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not remove:
        _validate_retention_days(days)
    client = get_logs_client(region)
    try:
        groups = []
        kwargs = {}
        if prefix:
            kwargs["logGroupNamePrefix"] = prefix
        while True:
            response = client.describe_log_groups(**kwargs)
            page = response.get("logGroups", [])
            for g in page:
                if only_unset and g.get("retentionInDays") is not None:
                    continue
                groups.append(g)
                if limit is not None and len(groups) >= limit:
                    break
            if limit is not None and len(groups) >= limit:
                break
            next_token = response.get("nextToken")
            if not next_token:
                break
            kwargs["nextToken"] = next_token
        if limit is not None:
            groups = groups[:limit]
        if not groups:
            typer.echo("No log groups to update.")
            return
        if not yes:
            confirm = typer.confirm(
                f"Update retention for {len(groups)} log group(s)?",
                default=False,
            )
            if not confirm:
                typer.echo("Aborted.")
                raise typer.Exit(0)
        success = 0
        failed = []
        for g in groups:
            name = g.get("logGroupName", "")
            try:
                if remove:
                    client.delete_retention_policy(logGroupName=name)
                else:
                    client.put_retention_policy(logGroupName=name, retentionInDays=days)
                success += 1
            except ClientError as e:
                failed.append((name, str(e)))
        if failed:
            for name, err in failed:
                typer.secho(f"Failed {name}: {err}", fg=typer.colors.RED)
        typer.echo(
            f"Updated {success} log group(s)."
            + (f" {len(failed)} failed." if failed else "")
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)


# --- list-streams ---


@app.command("list-streams")
def list_streams(
    log_group: str = typer.Option(..., "--log-group", "-g", help="Log group name"),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max number of streams"),
    order: str = typer.Option(
        "LastEventTime",
        "--order",
        help="Order by: LastEventTime or LogStreamName",
    ),
):
    """List log streams in a log group."""
    client = get_logs_client(region)
    try:
        response = client.describe_log_streams(
            logGroupName=log_group,
            orderBy=order,
            descending=True,
            limit=limit,
        )
        streams = response.get("logStreams", [])
        if not streams:
            typer.echo("No log streams found.")
            return
        table = Table(title=f"Log streams in {log_group}")
        table.add_column("Stream name", style="cyan")
        table.add_column("Last event", style="dim")
        for s in streams:
            last = s.get("lastEventTimestamp")
            last_str = (
                datetime.fromtimestamp(last / 1000.0, tz=timezone.utc).isoformat()
                if last
                else ""
            )
            table.add_row(s.get("logStreamName", ""), last_str)
        console.print(table)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)


# --- read ---


@app.command("read")
def read_logs(
    log_group: str = typer.Option(..., "--log-group", "-g", help="Log group name"),
    log_stream: str = typer.Option(
        None, "--log-stream", "-s", help="Single log stream"
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    start_time: int = typer.Option(
        None, "--start-time", help="Start time (Unix timestamp in milliseconds)"
    ),
    end_time: int = typer.Option(
        None, "--end-time", help="End time (Unix timestamp in milliseconds)"
    ),
    since: str = typer.Option(
        None, "--since", help="Relative start time (e.g. 30m, 1h, 2d)"
    ),
    filter_pattern: str = typer.Option(
        None, "--filter-pattern", "-f", help="Filter pattern for log events"
    ),
    limit: int = typer.Option(100, "--limit", "-n", help="Max number of events"),
):
    """Read log events from a log group (and optionally a single stream)."""
    client = get_logs_client(region)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = start_time
    end_ms = end_time
    if since:
        start_ms = _parse_since(since)
    if start_ms is not None and end_ms is None:
        end_ms = now_ms
    # When no time range is given, default to last 24h so we show recent activity
    if start_ms is None and end_ms is None:
        start_ms = now_ms - (24 * 60 * 60 * 1000)
        end_ms = now_ms

    try:
        if log_stream:
            # Single stream: get_log_events
            kwargs = {
                "logGroupName": log_group,
                "logStreamName": log_stream,
                "limit": limit,
            }
            if start_ms is not None:
                kwargs["startTime"] = start_ms
            if end_ms is not None:
                kwargs["endTime"] = end_ms
            response = client.get_log_events(**kwargs)
            events = response.get("events", [])
        else:
            # All streams: filter_log_events
            kwargs = {
                "logGroupName": log_group,
                "limit": limit,
            }
            if start_ms is not None:
                kwargs["startTime"] = start_ms
            if end_ms is not None:
                kwargs["endTime"] = end_ms
            if filter_pattern:
                kwargs["filterPattern"] = filter_pattern
            response = client.filter_log_events(**kwargs)
            events = response.get("events", [])

        if not events:
            typer.echo("No log events found.")
            return
        for evt in events:
            ts = evt.get("timestamp")
            msg = evt.get("message", "").rstrip()
            ts_str = (
                datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
                if ts is not None
                else ""
            )
            console.print(f"[dim]{ts_str}[/dim] {msg}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)
