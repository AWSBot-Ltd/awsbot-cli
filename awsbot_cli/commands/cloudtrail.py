"""CloudTrail auditing: search for deletion/disposal events for compliance evidence."""

import csv
import os
import re
from datetime import datetime, timezone

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from awsbot_cli.utils.cloudtrail import (
    check_trail_data_events,
    discover_organization_id,
    ensure_cloudtrail_athena_table,
    find_deletion_events,
    find_deletion_events_athena,
    resolve_athena_output_location,
)

app = typer.Typer(help="Search CloudTrail for deletion/disposal events")
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


def get_cloudtrail_client(region: str | None = None):
    """Return a boto3 CloudTrail client for the given region."""
    if region is None:
        region = _default_region()
    return boto3.client("cloudtrail", region_name=region)


def _parse_date(label: str, value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        typer.secho(f"{label} must be in YYYY-MM-DD format.", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("deletion-audit")
def deletion_audit(
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)"),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    export_csv: str = typer.Option(None, "--export-csv", help="Export results to CSV"),
    athena_output: str = typer.Option(
        None,
        "--athena-output",
        help="S3 URI for Athena query results (e.g. s3://my-bucket/athena/). "
        "Needed to search beyond the 90-day CloudTrail Event History window; "
        "auto-detected from the workgroup's configured output location if omitted.",
    ),
    athena_database: str = typer.Option(
        "awsbot_audit",
        "--athena-database",
        help="Glue database for the CloudTrail Athena table",
    ),
    athena_workgroup: str = typer.Option(
        "primary", "--athena-workgroup", help="Athena workgroup to run queries in"
    ),
    trail: str = typer.Option(
        None,
        "--trail",
        help="Trail name to source S3 logs from for Athena "
        "(default: first trail with an S3 bucket)",
    ),
):
    """
    Search CloudTrail for destructive/deletion events (S3, EBS, RDS, ACM, EC2, etc.)
    in a date range. Useful as audit evidence for data/media disposal requests.

    Dates within the last ~90 days use CloudTrail Event History directly.
    Older start dates automatically fall back to querying a Trail's raw S3
    logs via Athena, if one is available.
    """
    start_dt = _parse_date("--start", start)
    end_dt = _parse_date("--end", end).replace(hour=23, minute=59, second=59)

    if start_dt > end_dt:
        typer.secho("--start must be before --end.", fg=typer.colors.RED)
        raise typer.Exit(1)

    needs_full_history = (datetime.now(timezone.utc) - start_dt).days > 90

    client = get_cloudtrail_client(region)

    try:
        trails = check_trail_data_events(client)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)

    if not trails:
        console.print(
            "[yellow]No CloudTrail trails found in this account/region.[/yellow]"
        )
    else:
        for t in trails:
            console.print(
                f"Trail: [cyan]{t['name']}[/cyan]  S3 bucket: {t['s3_bucket'] or '-'}"
            )
            if not t["s3_data_events_enabled"]:
                console.print(
                    "  [yellow]S3 object-level (data) events are not logged on this "
                    "trail — individual DeleteObject calls won't appear below.[/yellow]"
                )

    use_athena = False
    chosen_trail = None
    resolved_output = None

    if needs_full_history:
        if trail:
            chosen_trail = next((t for t in trails if t["name"] == trail), None)
            if not chosen_trail:
                typer.secho(f"Trail '{trail}' not found.", fg=typer.colors.RED)
                raise typer.Exit(1)
        else:
            chosen_trail = next((t for t in trails if t["s3_bucket"]), None)

        athena_client = boto3.client("athena", region_name=region or _default_region())
        resolved_output = resolve_athena_output_location(
            athena_client, athena_workgroup, athena_output
        )

        if not chosen_trail or not chosen_trail["s3_bucket"]:
            console.print(
                "[yellow]Warning:[/yellow] --start is more than 90 days ago and no trail "
                "with an S3 bucket was found, so results below may be incomplete "
                "(CloudTrail Event History only retains ~90 days)."
            )
        elif not resolved_output:
            console.print(
                "[yellow]Warning:[/yellow] --start is more than 90 days ago. Pass "
                "--athena-output s3://bucket/prefix/ (or configure one on the "
                f"'{athena_workgroup}' workgroup) to query the full history via Athena "
                f"over trail '{chosen_trail['name']}'s S3 logs; results below may be "
                "incomplete for now."
            )
        else:
            use_athena = True

    if use_athena:
        try:
            account_id = boto3.client(
                "sts", region_name=region or _default_region()
            ).get_caller_identity()["Account"]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = e.response.get("Error", {}).get("Message", str(e))
            typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
            raise typer.Exit(1)

        prefix = chosen_trail.get("s3_key_prefix")
        prefix_part = f"{prefix}/" if prefix else ""
        query_region = region or _default_region()

        if chosen_trail.get("is_organization_trail"):
            s3_client = boto3.client("s3", region_name=query_region)
            org_id = discover_organization_id(
                s3_client, chosen_trail["s3_bucket"], prefix
            )
            if not org_id:
                typer.secho(
                    "Could not find an organization-id folder under "
                    f"s3://{chosen_trail['s3_bucket']}/{prefix_part}AWSLogs/ for "
                    f"organization trail '{chosen_trail['name']}'.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            s3_location = (
                f"s3://{chosen_trail['s3_bucket']}/{prefix_part}"
                f"AWSLogs/{org_id}/{account_id}/CloudTrail/{query_region}/"
            )
        else:
            s3_location = (
                f"s3://{chosen_trail['s3_bucket']}/{prefix_part}"
                f"AWSLogs/{account_id}/CloudTrail/{query_region}/"
            )
        table_name = "cloudtrail_" + re.sub(
            r"[^a-z0-9_]", "_", chosen_trail["name"].lower()
        )

        console.print(
            f"[dim]Querying Athena over {s3_location} "
            f"(table {athena_database}.{table_name})...[/dim]"
        )
        try:
            ensure_cloudtrail_athena_table(
                athena_client,
                athena_database,
                table_name,
                s3_location,
                resolved_output,
                athena_workgroup,
            )
            events = find_deletion_events_athena(
                athena_client,
                athena_database,
                table_name,
                resolved_output,
                start_dt,
                end_dt,
                workgroup=athena_workgroup,
            )
        except (ClientError, RuntimeError, TimeoutError) as e:
            typer.secho(f"Athena query failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)
    else:
        try:
            events = find_deletion_events(client, start_dt, end_dt)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = e.response.get("Error", {}).get("Message", str(e))
            typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
            raise typer.Exit(1)

    if not events:
        console.print(
            f"\n[green]No deletion/disposal events found between {start} and {end}.[/green]"
        )
        console.print(
            "[dim]This output can serve as your evidence of zero disposals for the period.[/dim]"
        )
        return

    table = Table(title=f"Deletion events {start} → {end}")
    table.add_column("Time (UTC)", style="dim")
    table.add_column("Event")
    table.add_column("Actor")
    table.add_column("Resource")

    rows_for_csv = []
    for evt in events:
        ts = evt.get("EventTime")
        ts_str = (
            ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
        )
        event_name = evt.get("EventName", "")
        actor = evt.get("Username", "")
        resource_str = (
            ", ".join(
                r.get("ResourceName", "")
                for r in evt.get("Resources", [])
                if r.get("ResourceName")
            )
            or "-"
        )
        table.add_row(ts_str, event_name, actor, resource_str)
        rows_for_csv.append(
            {
                "Time": ts_str,
                "Event": event_name,
                "Actor": actor,
                "Resource": resource_str,
            }
        )

    console.print(table)
    console.print(f"\n[bold]{len(events)}[/bold] deletion event(s) found.")

    if export_csv:
        with open(export_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["Time", "Event", "Actor", "Resource"]
            )
            writer.writeheader()
            writer.writerows(rows_for_csv)
        console.print(f"[green]Exported to {export_csv}[/green]")
