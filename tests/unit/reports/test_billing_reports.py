from unittest.mock import MagicMock, patch
from awsbot_cli.reports.billing import get_monthly_cost_by_service, get_billing_data


# --- Mock Data Helpers ---


def mock_ce_response(service_name, amount, start_date):
    """Helper to generate a standard Cost Explorer response group."""
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": start_date, "End": "2026-02-01"},
                "Groups": [
                    {
                        "Keys": [service_name],
                        "Metrics": {
                            "UnblendedCost": {"Amount": str(amount), "Unit": "USD"}
                        },
                    }
                ],
            }
        ]
    }


# --- Tests ---


@patch("awsbot_cli.reports.billing.boto3.client")
def test_get_monthly_cost_by_service(mock_boto):
    """Verify service grouping and descending sort order."""
    mock_client = MagicMock()
    mock_boto.return_value = mock_client

    # Simulate two services with different costs
    mock_client.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-01-01", "End": "2026-02-01"},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud"],
                        "Metrics": {"UnblendedCost": {"Amount": "50.0"}},
                    },
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "150.0"}},
                    },
                ],
            }
        ]
    }

    data, headers = get_monthly_cost_by_service()

    # Verify headers
    assert headers == ["Service", "Cost (USD)", "Month Start"]

    # Verify sorting (S3 should be first because 150 > 50)
    assert data[0][0] == "Amazon Simple Storage Service"
    assert data[0][1] == 150.0
    assert data[1][0] == "Amazon Elastic Compute Cloud"


@patch("awsbot_cli.reports.billing.boto3.client")
def test_get_billing_data_pivoting(mock_boto):
    """
    Verify the transformation from 'Time-Series' data to a
    'Pivoted Table' with a total row.
    """
    mock_client = MagicMock()
    mock_boto.return_value = mock_client

    # Simulate data across two different months for the same service
    mock_client.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-01-01"},
                "Groups": [
                    {"Keys": ["EC2"], "Metrics": {"UnblendedCost": {"Amount": "10.0"}}}
                ],
            },
            {
                "TimePeriod": {"Start": "2026-02-01"},
                "Groups": [
                    {"Keys": ["EC2"], "Metrics": {"UnblendedCost": {"Amount": "20.0"}}}
                ],
            },
        ]
    }

    result = get_billing_data()

    # 1. Verify Headers (Service, Months, Total)
    assert "Service" in result["headers"]
    assert "2026-01" in result["headers"]
    assert "2026-02" in result["headers"]
    assert "Total" in result["headers"]

    # 2. Verify Data Row (EC2)
    ec2_row = next(r for r in result["data"] if r["Service"] == "EC2")
    assert ec2_row["2026-01"] == "$10.00"
    assert ec2_row["2026-02"] == "$20.00"
    assert ec2_row["Total"] == "$30.00"

    # 3. Verify Grand Total Row
    total_row = next(r for r in result["data"] if r["Service"] == "--- TOTAL ---")
    assert total_row["Total"] == "$30.00"
    assert result["total_spend"] == 30.0


def test_get_billing_data_date_parsing():
    """Verify that manual date strings are correctly parsed into the query."""
    with patch("awsbot_cli.reports.billing.boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        mock_client.get_cost_and_usage.return_value = {"ResultsByTime": []}

        get_billing_data(start_date="2025-12-01", end_date="2026-01-01")

        # Capture arguments passed to get_cost_and_usage
        args, kwargs = mock_client.get_cost_and_usage.call_args
        assert kwargs["TimePeriod"]["Start"] == "2025-12-01"
        assert kwargs["TimePeriod"]["End"] == "2026-01-01"
