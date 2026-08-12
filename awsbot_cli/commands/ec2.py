"""EC2 instance inventory: table for quick reading, CSV/Excel export."""

import csv
import os

import boto3
import typer
from botocore.exceptions import ClientError
from openpyxl import Workbook
from rich.console import Console
from rich.table import Table

from awsbot_cli.utils.ec2 import (
    list_instances,
    security_groups_open_to_internet,
    ssm_enrichment,
)

app = typer.Typer(help="EC2 instance inventory")
console = Console()

COLUMNS = [
    "Instance ID",
    "Name",
    "Type",
    "State",
    "AZ",
    "Private IP",
    "Public IP",
    "Launch Time",
    "OS",
    "IMDSv2 Enforced",
    "IAM Instance Profile",
    "Security Groups",
    "Internet-Exposed",
    "Patch Compliance",
]

# Subset shown in the terminal table -- CSV/xlsx export always carries all of COLUMNS.
TABLE_COLUMNS = [
    "Instance ID",
    "Name",
    "State",
    "OS",
    "IMDSv2 Enforced",
    "Internet-Exposed",
    "Patch Compliance",
]


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


def get_ec2_client(region: str | None = None):
    """Return a boto3 EC2 client for the given region."""
    if region is None:
        region = _default_region()
    return boto3.client("ec2", region_name=region)


def get_ssm_client(region: str | None = None):
    """Return a boto3 SSM client for the given region."""
    if region is None:
        region = _default_region()
    return boto3.client("ssm", region_name=region)


@app.command("list")
def list_command(
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    export_csv: str = typer.Option(None, "--export-csv", help="Export results to CSV"),
    export_xlsx: str = typer.Option(
        None, "--export-xlsx", help="Export results to an Excel (.xlsx) file"
    ),
):
    """List EC2 instances in a region as a table, optionally exporting to CSV/Excel."""
    client = get_ec2_client(region)
    instances = list_instances(client)

    if not instances:
        console.print(
            f"[yellow]No EC2 instances found in {region or _default_region()}.[/yellow]"
        )
        return

    sg_ids = sorted({gid for inst in instances for gid in inst["_sg_ids"]})
    try:
        sg_map = security_groups_open_to_internet(client, sg_ids)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        typer.secho(f"AWS Error ({code}): {msg}", fg=typer.colors.RED)
        raise typer.Exit(1)

    for inst in instances:
        gids = inst.pop("_sg_ids", [])
        inst["Internet-Exposed"] = (
            "Yes" if any(sg_map.get(g, False) for g in gids) else "No"
        )

    ssm_ok = True
    try:
        ssm_map = ssm_enrichment(
            get_ssm_client(region), [i["Instance ID"] for i in instances]
        )
    except ClientError as e:
        ssm_ok = False
        ssm_map = {}
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        console.print(
            f"[yellow]Warning: SSM enrichment failed ({code}): {msg}. "
            "'OS' will show basic EC2 metadata only and 'Patch Compliance' will show '-'.[/yellow]"
        )

    for inst in instances:
        if not ssm_ok:
            inst["Patch Compliance"] = "-"
            continue
        info = ssm_map.get(inst["Instance ID"])
        if info is None:
            inst["Patch Compliance"] = "Not SSM-managed"
        else:
            if info.get("PlatformName"):
                inst["OS"] = (
                    f"{info['PlatformName']} {info.get('PlatformVersion', '')}".strip()
                )
            inst["Patch Compliance"] = info.get("PatchComplianceStatus", "UNKNOWN")

    table = Table(title=f"EC2 Instances ({region or _default_region()})")
    for col in TABLE_COLUMNS:
        table.add_column(col)
    for inst in instances:
        table.add_row(*(str(inst[c]) for c in TABLE_COLUMNS))

    console.print(table)
    console.print(f"\n[bold]{len(instances)}[/bold] instance(s) found.")

    if export_csv:
        with open(export_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(instances)
        console.print(f"[green]Exported to {export_csv}[/green]")

    if export_xlsx:
        wb = Workbook()
        ws = wb.active
        ws.title = "EC2 Instances"
        ws.append(COLUMNS)
        for inst in instances:
            ws.append([inst[c] for c in COLUMNS])
        wb.save(export_xlsx)
        console.print(f"[green]Exported to {export_xlsx}[/green]")
