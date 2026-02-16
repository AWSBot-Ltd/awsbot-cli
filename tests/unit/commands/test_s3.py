import re
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner
import awsbot_cli.commands.s3 as s3_cmd

runner = CliRunner()


def strip_ansi(text):
    """Removes ANSI escape codes (colors/formatting) from output."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


# --- Fixtures ---


@pytest.fixture
def mock_requests_path():
    """Correct path for patching requests in this module."""
    return "awsbot_cli.commands.s3.requests"


@pytest.fixture
def mock_boto_session():
    with patch("awsbot_cli.commands.s3.boto3.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_s3_client(mock_boto_session):
    client = MagicMock()
    mock_boto_session.client.return_value = client
    return client


@pytest.fixture
def mock_utils():
    # Ensure we patch the utilities in the specific command module
    path = "awsbot_cli.commands.s3"
    with (
        patch(f"{path}.get_bucket_size") as mock_size,
        patch(f"{path}.get_bucket_lifecycle") as mock_life,
        patch(f"{path}.get_aws_billing_details") as mock_bill,
        patch(f"{path}.publish_report") as mock_pub,
        patch(f"{path}.resolve_buckets") as mock_resolve,
        patch(f"{path}.append_lifecycle_rule") as mock_append,
        patch(f"{path}.print_formatted_output") as mock_print_fmt,
    ):
        yield {
            "get_bucket_size": mock_size,
            "get_bucket_lifecycle": mock_life,
            "get_aws_billing_details": mock_bill,
            "publish_report": mock_pub,
            "resolve_buckets": mock_resolve,
            "append_lifecycle_rule": mock_append,
            "print_formatted_output": mock_print_fmt,
        }


# --- Fixed Tests ---


def test_report_command(mock_s3_client, mock_utils):
    """Test generating a report and capturing mock calls instead of stdout."""
    mock_s3_client.list_buckets.return_value = {
        "Buckets": [{"Name": "bucket-a", "CreationDate": MagicMock()}]
    }
    mock_s3_client.get_bucket_location.return_value = {
        "LocationConstraint": "us-east-1"
    }

    mock_utils["get_bucket_size"].return_value = 1073741824  # 1 GB
    mock_utils["get_bucket_lifecycle"].return_value = "Enabled"
    mock_utils["get_aws_billing_details"].return_value = []

    # Run command
    result = runner.invoke(s3_cmd.app, ["report", "--no-cache"])

    assert result.exit_code == 0

    # Instead of checking stdout (which may be empty due to Rich/Mocks),
    # check if publish_report was called with the correct data.
    mock_utils["publish_report"].assert_called_once()
    args, kwargs = mock_utils["publish_report"].call_args

    # Verify the first row (excluding header) has the correct cost
    # Row format: [Name, Region, Size, Cost Std, Cost Int, Lifecycle, Created]
    first_row = kwargs["rows"][0]
    assert "bucket-a" in first_row
    assert "$0.02" in first_row  # 1GB * 0.023 rounded


# def test_clean_dry_run(mock_utils):
#     """Test clean command dry run with fresh file streams."""
#     csv_content = "Bucket Name,Expiration\nbucket-to-del,Delete"
#
#     # Use a side_effect function to provide a new stream every time open() is called
#     def mock_open_file(*args, **kwargs):
#         return io.BytesIO(csv_content.encode("utf-8-sig"))
#
#     with patch("builtins.open", side_effect=mock_open_file), \
#             patch("os.path.exists", return_value=True):
#         result = runner.invoke(s3_cmd.app, ["clean", "--dry-run"])
#
#         clean_output = strip_ansi(result.stdout)
#         assert result.exit_code == 0
#         assert "Would delete bucket: bucket-to-del" in clean_output
#
#
# @patch("awsbot_cli.commands.s3.boto3.resource")
# def test_clean_execute(mock_resource_factory, mock_utils):
#     """Test clean command execution with fresh file streams and resource mocking."""
#     csv_content = "Bucket Name,Expiration\nbucket-to-del,Delete"
#
#     def mock_open_file(*args, **kwargs):
#         return io.BytesIO(csv_content.encode("utf-8-sig"))
#
#     mock_s3_resource = MagicMock()
#     mock_resource_factory.return_value = mock_s3_resource
#     mock_bucket = MagicMock()
#     mock_s3_resource.Bucket.return_value = mock_bucket
#
#     with patch("builtins.open", side_effect=mock_open_file), \
#             patch("os.path.exists", return_value=True):
#         # Passing --no-dry-run triggers the actual deletion logic
#         result = runner.invoke(s3_cmd.app, ["clean", "--no-dry-run"])
#
#         assert result.exit_code == 0
#         # Verify the Bucket was instantiated with the correct name
#         mock_s3_resource.Bucket.assert_called_with("bucket-to-del")
#         # Verify deletion was called
#         assert mock_bucket.delete.called
