"""Unit tests for CloudTrail auditing CLI commands."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import awsbot_cli.commands.cloudtrail

PATCH_PATH = "awsbot_cli.commands.cloudtrail"
runner = CliRunner()

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_ct_client():
    """Patch get_cloudtrail_client to return a MagicMock."""
    with patch(f"{PATCH_PATH}.get_cloudtrail_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


def test_deletion_audit_rejects_bad_date(mock_ct_client):
    result = runner.invoke(
        awsbot_cli.commands.cloudtrail.app,
        ["--start", "not-a-date", "--end", "2026-06-17"],
    )
    assert result.exit_code == 1


def test_deletion_audit_rejects_start_after_end(mock_ct_client):
    result = runner.invoke(
        awsbot_cli.commands.cloudtrail.app,
        ["--start", "2026-06-17", "--end", "2026-03-17"],
    )
    assert result.exit_code == 1


def test_deletion_audit_no_events(mock_ct_client):
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=[]),
        patch(f"{PATCH_PATH}.find_deletion_events", return_value=[]),
    ):
        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            ["--start", "2026-03-17", "--end", "2026-06-17"],
        )
    assert result.exit_code == 0
    assert "No deletion/disposal events found" in result.stdout


def test_deletion_audit_with_events_and_csv(mock_ct_client, tmp_path):
    csv_path = tmp_path / "out.csv"
    events = [
        {
            "EventId": "1",
            "EventName": "DeleteBucket",
            "EventTime": datetime(2026, 4, 2, tzinfo=timezone.utc),
            "Username": "alice",
            "Resources": [{"ResourceName": "old-bucket"}],
        }
    ]
    trails = [
        {
            "name": "main-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": False,
        }
    ]
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails),
        patch(f"{PATCH_PATH}.find_deletion_events", return_value=events),
        # --start is >90 days before "today" for this fixed date, which would
        # otherwise trigger the Athena path and hit real AWS via boto3.client;
        # force the "no output location configured" fallback instead.
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value=None),
    ):
        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            [
                "--start",
                "2026-03-17",
                "--end",
                "2026-06-17",
                "--export-csv",
                str(csv_path),
            ],
        )

    assert result.exit_code == 0
    assert "old-bucket" in result.stdout
    assert "DeleteBucket" in result.stdout
    assert "S3 object-level (data) events are not logged" in result.stdout
    assert csv_path.exists()
    content = csv_path.read_text()
    assert "old-bucket" in content
    assert "DeleteBucket" in content


def test_deletion_audit_warns_outside_retention_window_no_trail(mock_ct_client):
    """>90 days old, no trail at all: falls back to Event History with a warning."""
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=[]),
        patch(f"{PATCH_PATH}.find_deletion_events", return_value=[]),
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value=None),
    ):
        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            ["--start", "2020-01-01", "--end", "2020-02-01"],
        )
    assert result.exit_code == 0
    assert "more than 90 days ago" in result.stdout
    assert "no trail with an S3 bucket" in result.stdout


def test_deletion_audit_warns_outside_retention_window_no_output(mock_ct_client):
    """>90 days old, trail exists but no Athena output location resolves."""
    trails = [
        {
            "name": "main-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
        }
    ]
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails),
        patch(f"{PATCH_PATH}.find_deletion_events", return_value=[]) as mock_find,
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value=None),
        patch(f"{PATCH_PATH}.ensure_cloudtrail_athena_table") as mock_ensure,
    ):
        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            ["--start", "2020-01-01", "--end", "2020-02-01"],
        )
    assert result.exit_code == 0
    assert "--athena-output" in result.stdout
    mock_find.assert_called_once()
    mock_ensure.assert_not_called()


def test_deletion_audit_uses_athena_when_output_available(mock_ct_client):
    """>90 days old, trail + resolved output location: Athena path is used."""
    trails = [
        {
            "name": "main-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
        }
    ]
    athena_events = [
        {
            "EventId": "evt-1",
            "EventName": "DeleteBucket",
            "EventTime": datetime(2020, 1, 15, tzinfo=timezone.utc),
            "Username": "alice",
            "Resources": [{"ResourceName": "old-bucket"}],
        }
    ]
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails),
        patch(f"{PATCH_PATH}.find_deletion_events") as mock_find,
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value="s3://out/"),
        patch(f"{PATCH_PATH}.ensure_cloudtrail_athena_table") as mock_ensure,
        patch(
            f"{PATCH_PATH}.find_deletion_events_athena", return_value=athena_events
        ) as mock_athena_find,
        patch(f"{PATCH_PATH}.boto3.client") as mock_boto_client,
    ):
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        mock_boto_client.return_value = mock_sts

        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            [
                "--start",
                "2020-01-01",
                "--end",
                "2020-02-01",
                "--athena-output",
                "s3://out/",
            ],
        )

    assert result.exit_code == 0
    assert "old-bucket" in result.stdout
    mock_find.assert_not_called()
    mock_ensure.assert_called_once()
    mock_athena_find.assert_called_once()
    ensure_args = mock_ensure.call_args[0]
    assert ensure_args[1] == "awsbot_audit"
    assert "AWSLogs/123456789012/CloudTrail/" in ensure_args[3]


def test_deletion_audit_trail_option_not_found(mock_ct_client):
    """--trail naming a trail that doesn't exist errors out."""
    trails = [
        {
            "name": "main-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
        }
    ]
    with patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails):
        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            [
                "--start",
                "2020-01-01",
                "--end",
                "2020-02-01",
                "--trail",
                "nonexistent-trail",
            ],
        )
    assert result.exit_code == 1


def test_deletion_audit_org_trail_uses_discovered_org_id(mock_ct_client):
    """Organization trails need AWSLogs/{org-id}/{account-id}/... in the S3 path."""
    trails = [
        {
            "name": "org-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
            "is_organization_trail": True,
        }
    ]
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails),
        patch(f"{PATCH_PATH}.find_deletion_events") as mock_find,
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value="s3://out/"),
        patch(
            f"{PATCH_PATH}.discover_organization_id", return_value="o-8s6ewnn3u5"
        ) as mock_discover,
        patch(f"{PATCH_PATH}.ensure_cloudtrail_athena_table") as mock_ensure,
        patch(f"{PATCH_PATH}.find_deletion_events_athena", return_value=[]),
        patch(f"{PATCH_PATH}.boto3.client") as mock_boto_client,
    ):
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        mock_boto_client.return_value = mock_sts

        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            [
                "--start",
                "2020-01-01",
                "--end",
                "2020-02-01",
                "--region",
                "us-east-1",
                "--athena-output",
                "s3://out/",
            ],
        )

    assert result.exit_code == 0
    mock_find.assert_not_called()
    mock_discover.assert_called_once()
    ensure_args = mock_ensure.call_args[0]
    assert (
        ensure_args[3]
        == "s3://logs-bucket/AWSLogs/o-8s6ewnn3u5/123456789012/CloudTrail/us-east-1/"
    )


def test_deletion_audit_org_trail_id_not_found_errors(mock_ct_client):
    """If the org-id folder can't be discovered, fail loudly instead of querying an empty table."""
    trails = [
        {
            "name": "org-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
            "is_organization_trail": True,
        }
    ]
    with (
        patch(f"{PATCH_PATH}.check_trail_data_events", return_value=trails),
        patch(f"{PATCH_PATH}.resolve_athena_output_location", return_value="s3://out/"),
        patch(f"{PATCH_PATH}.discover_organization_id", return_value=None),
        patch(f"{PATCH_PATH}.ensure_cloudtrail_athena_table") as mock_ensure,
        patch(f"{PATCH_PATH}.boto3.client") as mock_boto_client,
    ):
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}
        mock_boto_client.return_value = mock_sts

        result = runner.invoke(
            awsbot_cli.commands.cloudtrail.app,
            [
                "--start",
                "2020-01-01",
                "--end",
                "2020-02-01",
                "--athena-output",
                "s3://out/",
            ],
        )

    assert result.exit_code == 1
    mock_ensure.assert_not_called()
