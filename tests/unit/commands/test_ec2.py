"""Unit tests for EC2 inventory CLI commands."""

from unittest.mock import MagicMock, patch

import openpyxl
import pytest
from botocore.exceptions import ClientError
from typer.testing import CliRunner

import awsbot_cli.commands.ec2

PATCH_PATH = "awsbot_cli.commands.ec2"
runner = CliRunner()

pytestmark = pytest.mark.unit

INSTANCE = {
    "Instance ID": "i-abc123",
    "Name": "web-1",
    "Type": "t3.micro",
    "State": "running",
    "AZ": "us-east-1a",
    "Private IP": "10.0.0.1",
    "Public IP": "1.2.3.4",
    "Launch Time": "2026-01-02 03:04:05",
    "OS": "Linux/UNIX",
    "IMDSv2 Enforced": "Yes",
    "IAM Instance Profile": "web-role",
    "Security Groups": "web-sg",
    "_sg_ids": ["sg-123"],
}


@pytest.fixture
def mock_ec2_client():
    with patch(f"{PATCH_PATH}.get_ec2_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_ssm_client():
    with patch(f"{PATCH_PATH}.get_ssm_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


@pytest.fixture
def no_enrichment(mock_ssm_client):
    """Patch SG/SSM enrichment to no-ops, for tests focused on other behavior."""
    with (
        patch(f"{PATCH_PATH}.security_groups_open_to_internet", return_value={}),
        patch(f"{PATCH_PATH}.ssm_enrichment", return_value={}),
    ):
        yield


def test_list_no_instances(mock_ec2_client):
    with patch(f"{PATCH_PATH}.list_instances", return_value=[]):
        result = runner.invoke(awsbot_cli.commands.ec2.app, [])

    assert result.exit_code == 0
    assert "No EC2 instances found" in result.stdout


def test_list_prints_table(mock_ec2_client, no_enrichment):
    with patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]):
        result = runner.invoke(awsbot_cli.commands.ec2.app, [])

    assert result.exit_code == 0
    # Rich truncates wide columns to terminal width, so check a column that fits.
    assert "web-1" in result.stdout
    assert "1 instance(s) found" in result.stdout


def test_list_exports_csv(mock_ec2_client, no_enrichment, tmp_path):
    csv_path = tmp_path / "out.csv"
    with patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    content = csv_path.read_text()
    assert "i-abc123" in content
    assert "Instance ID" in content


def test_list_exports_xlsx(mock_ec2_client, no_enrichment, tmp_path):
    xlsx_path = tmp_path / "out.xlsx"
    with patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-xlsx", str(xlsx_path)]
        )

    assert result.exit_code == 0
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == tuple(awsbot_cli.commands.ec2.COLUMNS)
    assert rows[1][0] == "i-abc123"


def test_list_internet_exposed_true(mock_ec2_client, mock_ssm_client, tmp_path):
    csv_path = tmp_path / "out.csv"
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(
            f"{PATCH_PATH}.security_groups_open_to_internet",
            return_value={"sg-123": True},
        ),
        patch(f"{PATCH_PATH}.ssm_enrichment", return_value={}),
    ):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    rows = csv_path.read_text().splitlines()
    assert "Yes" in rows[1]


def test_list_internet_exposed_false(mock_ec2_client, mock_ssm_client, tmp_path):
    csv_path = tmp_path / "out.csv"
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(
            f"{PATCH_PATH}.security_groups_open_to_internet",
            return_value={"sg-123": False},
        ),
        patch(f"{PATCH_PATH}.ssm_enrichment", return_value={}),
    ):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    rows = csv_path.read_text().splitlines()
    assert "No" in rows[1]


def test_list_ssm_managed_overlays_os_and_patch_status(
    mock_ec2_client, mock_ssm_client, tmp_path
):
    csv_path = tmp_path / "out.csv"
    ssm_info = {
        "i-abc123": {
            "PlatformName": "Ubuntu",
            "PlatformVersion": "22.04",
            "PatchComplianceStatus": "NON_COMPLIANT",
        }
    }
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(f"{PATCH_PATH}.security_groups_open_to_internet", return_value={}),
        patch(f"{PATCH_PATH}.ssm_enrichment", return_value=ssm_info),
    ):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    content = csv_path.read_text()
    assert "Ubuntu 22.04" in content
    assert "NON_COMPLIANT" in content


def test_list_ssm_unmanaged_instance(mock_ec2_client, mock_ssm_client, tmp_path):
    csv_path = tmp_path / "out.csv"
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(f"{PATCH_PATH}.security_groups_open_to_internet", return_value={}),
        patch(f"{PATCH_PATH}.ssm_enrichment", return_value={}),
    ):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    assert "Not SSM-managed" in csv_path.read_text()


def test_list_ssm_failure_degrades_gracefully(
    mock_ec2_client, mock_ssm_client, tmp_path
):
    csv_path = tmp_path / "out.csv"
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(f"{PATCH_PATH}.security_groups_open_to_internet", return_value={}),
        patch(
            f"{PATCH_PATH}.ssm_enrichment",
            side_effect=ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                "DescribeInstanceInformation",
            ),
        ),
    ):
        result = runner.invoke(
            awsbot_cli.commands.ec2.app, ["--export-csv", str(csv_path)]
        )

    assert result.exit_code == 0
    assert "Warning: SSM enrichment failed" in result.stdout
    rows = csv_path.read_text().splitlines()
    assert "i-abc123" in rows[1]
    assert rows[1].strip().endswith("-")


def test_list_security_group_lookup_failure_is_fatal(mock_ec2_client, mock_ssm_client):
    with (
        patch(f"{PATCH_PATH}.list_instances", return_value=[dict(INSTANCE)]),
        patch(
            f"{PATCH_PATH}.security_groups_open_to_internet",
            side_effect=ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                "DescribeSecurityGroups",
            ),
        ),
    ):
        result = runner.invoke(awsbot_cli.commands.ec2.app, [])

    assert result.exit_code == 1
    assert "AWS Error" in result.stdout
