import pytest
from typer.testing import CliRunner
from unittest.mock import patch

# --- IMPORT YOUR APP ---
# Ensure this matches your directory structure
from awsbot_cli.commands.billing import app

runner = CliRunner()

# --- CONFIGURATION ---
# Adjust this path to match where billing.py lives.
# Example: "awsbot_cli.commands.billing"
PATCH_PATH = "awsbot_cli.commands.billing"

# --- TEST DATA ---
MOCK_BILLING_DATA = {
    "start_date": "2023-10-01",
    "end_date": "2023-10-31",
    "total_spend": 123.456,
    "headers": ["Service", "Cost"],
    "data": [["Amazon EC2", "$50.00"], ["Amazon S3", "$73.45"]],
}

MOCK_REPORT_ROWS = [["EC2", "50.00"], ["S3", "73.45"]]
MOCK_REPORT_HEADERS = ["Service", "Cost"]


# --- TESTS ---


@pytest.mark.unit
@pytest.mark.billing
def test_show_command_prints_table():
    """
    Test that 'show' fetches data and passes it to the printer
    without actually hitting AWS.
    """
    # 1. Mock the data fetcher
    # 2. Mock the output printer (so we don't spam the test console)
    with (
        patch(f"{PATCH_PATH}.billing.get_billing_data") as mock_get_data,
        patch(f"{PATCH_PATH}.print_formatted_output") as mock_print,
    ):
        # Configure the mock to return our fake data
        mock_get_data.return_value = MOCK_BILLING_DATA

        # Run the command
        result = runner.invoke(app, ["show", "--service", "EC2"])

        # --- Assertions ---
        assert result.exit_code == 0

        # Check if the specific text defined in the command is present
        assert "Grand Total: $123.46" in result.stdout  # Note the rounding .2f
        assert "AWS Billing (2023-10-01 to 2023-10-31)" in result.stdout

        # Verify arguments passed to the fetcher
        mock_get_data.assert_called_once_with(
            start_date=None, end_date=None, service_filter="EC2"
        )

        # Verify the table printer was called with the correct list
        mock_print.assert_called_once_with(
            MOCK_BILLING_DATA["data"], headers=MOCK_BILLING_DATA["headers"]
        )


@pytest.mark.unit
@pytest.mark.billing
def test_show_command_handles_no_data():
    """Test behavior when API returns empty data."""
    with patch(f"{PATCH_PATH}.billing.get_billing_data") as mock_get_data:
        mock_get_data.return_value = None  # Simulate no data found

        result = runner.invoke(app, ["show"])

        assert result.exit_code == 0
        # Should NOT print the header or Grand Total if data is None
        assert "Grand Total" not in result.stdout


@pytest.mark.unit
@pytest.mark.billing
@pytest.mark.reporting
def test_report_command_local_only():
    """
    Test that the 'report' command calls the publisher
    with local_only=True when the flag is passed.
    """
    with (
        patch(f"{PATCH_PATH}.billing.get_monthly_cost_by_service") as mock_get_cost,
        patch(f"{PATCH_PATH}.publish_report") as mock_publish,
    ):
        mock_get_cost.return_value = (MOCK_REPORT_ROWS, MOCK_REPORT_HEADERS)

        result = runner.invoke(app, ["report", "--local"])

        assert result.exit_code == 0

        # Ensure we called the publish utility correctly
        mock_publish.assert_called_once_with(
            MOCK_REPORT_ROWS,
            MOCK_REPORT_HEADERS,
            "AWSBOT_Billing_Report",
            share_email=None,
            local_only=True,  # <--- Critical assertion
        )


@pytest.mark.unit
@pytest.mark.billing
@pytest.mark.reporting
def test_report_command_with_share():
    """
    Test that providing a share email passes it to the publisher.
    """
    with (
        patch(f"{PATCH_PATH}.billing.get_monthly_cost_by_service") as mock_get_cost,
        patch(f"{PATCH_PATH}.publish_report") as mock_publish,
    ):
        mock_get_cost.return_value = (MOCK_REPORT_ROWS, MOCK_REPORT_HEADERS)

        result = runner.invoke(app, ["report", "--share", "manager@company.com"])

        assert result.exit_code == 0

        mock_publish.assert_called_once_with(
            MOCK_REPORT_ROWS,
            MOCK_REPORT_HEADERS,
            "AWSBOT_Billing_Report",
            share_email="manager@company.com",  # <--- Critical assertion
            local_only=False,
        )
