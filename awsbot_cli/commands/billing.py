import typer

from awsbot_cli.reports import billing
from awsbot_cli.utils.logger import print_formatted_output
from awsbot_cli.utils.reporter import publish_report  # <--- Using our new util

app = typer.Typer(help="AWS Billing and Cost Management")


@app.command("show")
def show(
    service: str = typer.Option(None, help="Filter by service name"),
    start: str = typer.Option(None, metavar="YYYY-MM-DD", help="Start date"),
    end: str = typer.Option(None, metavar="YYYY-MM-DD", help="End date"),
):
    """Show current AWS spend."""
    data = billing.get_billing_data(
        start_date=start, end_date=end, service_filter=service
    )
    if data:
        print(f"\n--- AWS Billing ({data['start_date']} to {data['end_date']}) ---")
        print(f"Grand Total: ${data['total_spend']:.2f}\n")
        print_formatted_output(data["data"], headers=data["headers"])


@app.command("report")
def report(
    share: str = typer.Option(None, help="Email to share Google Sheet with"),
    local: bool = typer.Option(False, help="Print to terminal only"),
):
    """Generate full billing report."""
    rows, headers = billing.get_monthly_cost_by_service()
    # Call the new utility directly
    publish_report(
        rows, headers, "AWSBOT_Billing_Report", share_email=share, local_only=local
    )
