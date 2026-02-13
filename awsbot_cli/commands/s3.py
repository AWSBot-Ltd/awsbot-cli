import csv
import json
import os
import uuid

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from awsbot_cli.reports.s3 import (
    get_aws_billing_details,
    get_bucket_lifecycle,
    get_bucket_size,
)
from awsbot_cli.utils.common import format_bytes
from awsbot_cli.utils.logger import print_formatted_output
from awsbot_cli.utils.reporter import publish_report
from awsbot_cli.utils.s3 import append_lifecycle_rule, resolve_buckets

console = Console()
app = typer.Typer(help="Manage S3 Buckets and Objects")

CACHE_FILE = ".s3_cache.json"

# --- Pricing Constants ---
RATE_STANDARD = 0.023
RATE_INTELLIGENT_TIER = 0.0125


def load_cache():
    """Loads cached metric data from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache_data):
    """Saves metric data to disk."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_data, f)
    except Exception as e:
        print(f"Warning: Could not save cache: {e}")


# --- COMMANDS ---


@app.command("clean")
def clean(
    csv_file: str = typer.Option(
        "life_cycle_buckets.csv", help="Path to lifecycle CSV"
    ),
    dry_run: bool = typer.Option(False, help="Simulate actions"),
):
    """Process S3 bucket cleanup based on CSV."""
    process_s3_cleanup(csv_file, dry_run=dry_run)


@app.command("report")
def report(
    region: str = typer.Option(None, help="Filter by region (Optional)"),
    export_csv: str = typer.Option(None, help="Export to CSV file path"),
    no_cache: bool = typer.Option(False, help="Force refresh of CloudWatch metrics"),
    google_sheet: bool = typer.Option(
        False, "--google-sheet", help="Upload report to Google Sheets"
    ),
    share_email: str = typer.Option(None, help="Email to share the Google Sheet with"),
):
    """Generate S3 usage report with Size, Costs, and AWS Billing Data."""
    session = boto3.Session()
    s3_client = session.client("s3")
    metric_cache = {} if no_cache else load_cache()

    print("🔍 Generating S3 Usage Report...")
    try:
        response = s3_client.list_buckets()
        buckets = response.get("Buckets", [])
        report_data = []

        total_bytes_sum = 0
        total_cost_std_sum = 0.0
        total_cost_int_sum = 0.0

        for b in buckets:
            name = b["Name"]
            creation_date = b["CreationDate"].strftime("%Y-%m-%d %H:%M")

            try:
                loc_resp = s3_client.get_bucket_location(Bucket=name)
                bucket_region = loc_resp["LocationConstraint"] or "us-east-1"
            except ClientError:
                bucket_region = "AccessDenied"

            if region and region != bucket_region:
                continue

            if bucket_region != "AccessDenied":
                size_bytes = get_bucket_size(
                    name, bucket_region, session, cache_data=metric_cache
                )
                size_fmt = format_bytes(size_bytes)
                lifecycle_status = get_bucket_lifecycle(s3_client, name)

                size_gb = size_bytes / (1024**3)
                cost_std = size_gb * RATE_STANDARD
                cost_int = size_gb * RATE_INTELLIGENT_TIER

                total_bytes_sum += size_bytes
                total_cost_std_sum += cost_std
                total_cost_int_sum += cost_int
            else:
                size_bytes = 0
                size_fmt = "N/A"
                lifecycle_status = "Unknown"
                cost_std = 0.0
                cost_int = 0.0

            report_data.append(
                {
                    "Bucket Name": name,
                    "Region": bucket_region,
                    "Size": size_fmt,
                    "Cost (Std)": f"${cost_std:.2f}",
                    "Cost (Int-Tier)": f"${cost_int:.2f}",
                    "Lifecycle": lifecycle_status,
                    "Bytes": size_bytes,
                    "Created": creation_date,
                }
            )

        if not no_cache:
            save_cache(metric_cache)

        report_data.sort(key=lambda x: x["Bytes"], reverse=True)

        if not report_data:
            print("No buckets found.")
            return

        report_data.append(
            {
                "Bucket Name": "--- TOTALS ---",
                "Region": "",
                "Size": format_bytes(total_bytes_sum),
                "Cost (Std)": f"${total_cost_std_sum:.2f}",
                "Cost (Int-Tier)": f"${total_cost_int_sum:.2f}",
                "Lifecycle": "",
                "Bytes": total_bytes_sum,
                "Created": "",
            }
        )

        billing_details = get_aws_billing_details(session)
        if billing_details:
            report_data.append(
                {
                    "Bucket Name": "--- BILLING BREAKDOWN ---",
                    "Region": "",
                    "Size": "",
                    "Cost (Std)": "",
                    "Cost (Int-Tier)": "",
                    "Lifecycle": "",
                    "Bytes": 0,
                    "Created": "",
                }
            )
            total_billing = 0.0
            for item in billing_details:
                report_data.append(
                    {
                        "Bucket Name": f"BILLING: {item['type']}",
                        "Region": "Global",
                        "Size": "",
                        "Cost (Std)": f"${item['amount']:.2f}",
                        "Cost (Int-Tier)": "",
                        "Lifecycle": "",
                        "Bytes": 0,
                        "Created": "",
                    }
                )
                total_billing += item["amount"]
            report_data.append(
                {
                    "Bucket Name": "--- TOTAL BILLING ---",
                    "Region": "",
                    "Size": "",
                    "Cost (Std)": f"${total_billing:.2f}",
                    "Cost (Int-Tier)": "",
                    "Lifecycle": "",
                    "Bytes": 0,
                    "Created": "",
                }
            )

        headers = [
            "Bucket Name",
            "Region",
            "Size",
            "Cost (Std)",
            "Cost (Int-Tier)",
            "Lifecycle",
            "Created",
        ]
        rows = [[row[h] for h in headers] for row in report_data]

        if publish_report:
            publish_report(
                rows=rows,
                headers=headers,
                title_prefix="S3_Report_With_Costs",
                share_email=share_email,
                local_only=(not google_sheet),
            )
        else:
            print_formatted_output(
                [{k: v for k, v in row.items() if k != "Bytes"} for row in report_data],
                headers,
            )

        if export_csv:
            with open(export_csv, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=report_data[0].keys())
                writer.writeheader()
                writer.writerows(report_data)
            print(f"\n✅ CSV Report exported to {export_csv}")

    except Exception as e:
        print(f"❌ Error generating report: {e}")


def process_s3_cleanup(csv_path, dry_run=True):
    if not os.path.exists(csv_path):
        print(f"Error: File {csv_path} not found.")
        return
    s3 = boto3.resource("s3")
    print(f"--- Starting S3 Cleanup (Dry Run: {dry_run}) ---")
    count = 0
    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                reader.fieldnames = [name.strip() for name in reader.fieldnames]
            for row in reader:
                bucket_name = row.get("Bucket Name")
                expiration = row.get("Expiration")
                if expiration and expiration.strip() == "Delete":
                    try:
                        if dry_run:
                            print(f"[DRY RUN] Would delete bucket: {bucket_name}")
                        else:
                            print(f"Processing bucket: {bucket_name}...")
                            bucket = s3.Bucket(bucket_name)
                            bucket.object_versions.all().delete()
                            bucket.delete()
                            print(f"  - SUCCESS: {bucket_name} deleted.")
                        count += 1
                    except ClientError as e:
                        print(f"  - ERROR processing {bucket_name}: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    print(f"--- Finished. Total items processed: {count} ---")


@app.command("apply-tiering")
def apply_tiering(
    days: int = typer.Option(0, help="Days before transition (0 = immediate)"),
    bucket: str = typer.Option(None, "--bucket", "-b", help="Target a specific bucket"),
    filter_keyword: str = typer.Option(
        None, "--filter", "-f", help="Target buckets containing keyword"
    ),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
):
    """
    Apply 'Intelligent-Tiering' lifecycle rule.
    Target: Specific bucket, filtered list, or ALL buckets.
    """
    session = boto3.Session()
    s3_client = session.client("s3")

    target_buckets = resolve_buckets(s3_client, bucket, filter_keyword)
    if not target_buckets:
        return

    print(f"Found {len(target_buckets)} buckets to process.")
    if not force:
        if not typer.confirm(
            f"Apply Intelligent-Tiering to {len(target_buckets)} buckets?"
        ):
            print("Aborted.")
            return

    count = 0
    rule = {
        "ID": "Auto-IntelligentTiering-Rule",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "Transitions": [{"Days": days, "StorageClass": "INTELLIGENT_TIERING"}],
    }

    print("--- Applying Tiering Rules ---")
    for b_name in target_buckets:
        if append_lifecycle_rule(s3_client, b_name, rule):
            print(f"  ✅ Tiering applied to {b_name}")
            count += 1
    print(f"--- Finished. Updated {count} buckets. ---")


@app.command("apply-expiration")
def apply_expiration(
    days: int = typer.Argument(..., help="Days until expiration"),
    bucket: str = typer.Option(None, "--bucket", "-b", help="Target a specific bucket"),
    filter_keyword: str = typer.Option(
        None, "--filter", "-f", help="Target buckets containing keyword"
    ),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
):
    """
    Apply Expiration (Delete) policy.
    * Automatically detects Versioning.
    * Non-Versioned: Deletes objects after N days.
    * Versioned: Deletes current versions (marker) AND permanently deletes non-current versions after N days.
    """
    if days < 1:
        print("❌ Days must be greater than 0.")
        return

    session = boto3.Session()
    s3_client = session.client("s3")

    target_buckets = resolve_buckets(s3_client, bucket, filter_keyword)
    if not target_buckets:
        return

    print(
        f"Found {len(target_buckets)} buckets to process for EXPIRATION (Delete in {days} days)."
    )
    if not force:
        if not typer.confirm(
            f"⚠️  WARNING: This will configure DELETION rules for {len(target_buckets)} buckets. Proceed?"
        ):
            print("Aborted.")
            return

    count = 0
    print("--- Applying Expiration Rules ---")

    for b_name in target_buckets:
        # Detect Versioning
        try:
            ver_resp = s3_client.get_bucket_versioning(Bucket=b_name)
            status = ver_resp.get(
                "Status", "Suspended"
            )  # 'Enabled' or 'Suspended' or None
        except ClientError:
            print(f"  ❌ Could not check versioning for {b_name}")
            continue

        rule_id = f"Auto-Expiration-{days}Days"

        # Base Rule
        rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": days},
        }

        # If Versioned, we must also clean up the noncurrent versions
        if status == "Enabled":
            rule["NoncurrentVersionExpiration"] = {"NoncurrentDays": days}
            print(
                f"  ℹ️  {b_name}: Versioning is ENABLED. Adding NoncurrentVersionExpiration."
            )
        else:
            print(f"  ℹ️  {b_name}: Versioning is OFF/SUSPENDED. Standard Expiration.")

        if append_lifecycle_rule(s3_client, b_name, rule):
            print(f"  ✅ Expiration rule applied to {b_name}")
            count += 1

    print(f"--- Finished. Updated {count} buckets. ---")


@app.command(name="create-bucket")
def create_bucket(
    name: str = typer.Argument(
        ..., help="The base name for the bucket (e.g. wordpress-prod)"
    ),
    region: str = typer.Option(
        "us-east-1", help="The AWS region to create the bucket in"
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="Whether to disable Block Public Access (not recommended)",
    ),
):
    """
    Create a new S3 bucket with a unique ID suffix.
    """
    s3 = boto3.client("s3", region_name=region)

    # Generate a unique ID (first 8 chars of a UUID)
    unique_id = str(uuid.uuid4())[:8]
    full_bucket_name = f"{name}-{unique_id}"

    try:
        with console.status(f"[bold green]Creating bucket {full_bucket_name}..."):
            # S3 CreateBucket configuration varies slightly by region
            create_args = {"Bucket": full_bucket_name}
            if region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {
                    "LocationConstraint": region
                }

            s3.create_bucket(**create_args)

            # Standard security: Block Public Access by default
            if not public:
                s3.put_public_access_block(
                    Bucket=full_bucket_name,
                    PublicAccessBlockConfiguration={
                        "BlockPublicAcls": True,
                        "IgnorePublicAcls": True,
                        "BlockPublicPolicy": True,
                        "RestrictPublicBuckets": True,
                    },
                )

            # Add a tag to track that it was managed by this CLI
            s3.put_bucket_tagging(
                Bucket=full_bucket_name,
                Tagging={"TagSet": [{"Key": "ManagedBy", "Value": "AWSBOT-CLI"}]},
            )

        console.print("\n[bold green]✓ Bucket created successfully![/bold green]")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Property", style="dim")
        table.add_column("Value")
        table.add_row("Bucket Name", f"[bold cyan]{full_bucket_name}[/bold cyan]")
        table.add_row("Region", region)
        table.add_row(
            "Public Access", "Blocked" if not public else "[red]Enabled[/red]"
        )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
