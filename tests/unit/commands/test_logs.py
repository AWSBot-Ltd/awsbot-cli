"""Unit tests for CloudWatch Logs CLI commands."""

from unittest.mock import MagicMock, patch

import pytest
import typer
from botocore.exceptions import ClientError
from typer.testing import CliRunner

import awsbot_cli.commands.logs

PATCH_PATH = "awsbot_cli.commands.logs"
runner = CliRunner()

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_logs_client():
    """Patch get_logs_client to return a MagicMock."""
    with patch(f"{PATCH_PATH}.get_logs_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


# --- list-groups ---


def test_list_groups_success(mock_logs_client):
    """Test list-groups with sample log groups; table has Name and Retention only."""
    mock_logs_client.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/aws/lambda/fn1", "retentionInDays": 30},
            {"logGroupName": "/aws/lambda/fn2"},
        ]
    }
    result = runner.invoke(awsbot_cli.commands.logs.app, ["list-groups"])
    assert result.exit_code == 0
    assert "/aws/lambda/fn1" in result.stdout
    assert "/aws/lambda/fn2" in result.stdout
    assert "30 days" in result.stdout
    assert "Never expire" in result.stdout
    assert "Retention" in result.stdout
    assert "ARN" not in result.stdout
    mock_logs_client.describe_log_groups.assert_called_once()
    call_kw = mock_logs_client.describe_log_groups.call_args[1]
    assert call_kw.get("limit") == 50


def test_list_groups_with_prefix(mock_logs_client):
    """Test list-groups with --prefix."""
    mock_logs_client.describe_log_groups.return_value = {"logGroups": []}
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["list-groups", "--prefix", "/aws/lambda"],
    )
    assert result.exit_code == 0
    call_kw = mock_logs_client.describe_log_groups.call_args[1]
    assert call_kw["logGroupNamePrefix"] == "/aws/lambda"


def test_list_groups_empty(mock_logs_client):
    """Test list-groups when no groups found."""
    mock_logs_client.describe_log_groups.return_value = {"logGroups": []}
    result = runner.invoke(awsbot_cli.commands.logs.app, ["list-groups"])
    assert result.exit_code == 0
    assert "No log groups found" in result.stdout


# --- set-retention ---


def test_set_retention_success(mock_logs_client):
    """Test set-retention with --days calls put_retention_policy."""
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention", "--log-group", "my-group", "--days", "30"],
    )
    assert result.exit_code == 0
    assert "Retention set to 30 days" in result.stdout
    assert "my-group" in result.stdout
    mock_logs_client.put_retention_policy.assert_called_once_with(
        logGroupName="my-group", retentionInDays=30
    )
    mock_logs_client.delete_retention_policy.assert_not_called()


def test_set_retention_remove(mock_logs_client):
    """Test set-retention with --remove calls delete_retention_policy."""
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention", "--log-group", "my-group", "--remove"],
    )
    assert result.exit_code == 0
    assert "Retention removed" in result.stdout
    mock_logs_client.delete_retention_policy.assert_called_once_with(
        logGroupName="my-group"
    )
    mock_logs_client.put_retention_policy.assert_not_called()


def test_set_retention_invalid_days(mock_logs_client):
    """Test set-retention with invalid --days raises BadParameter."""
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention", "--log-group", "my-group", "--days", "99"],
    )
    assert result.exit_code != 0
    mock_logs_client.put_retention_policy.assert_not_called()


def test_set_retention_client_error(mock_logs_client):
    """Test set-retention surfaces ClientError."""
    mock_logs_client.put_retention_policy.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "Log group not found",
            }
        },
        "put_retention_policy",
    )
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention", "--log-group", "missing", "--days", "30"],
    )
    assert result.exit_code == 1
    assert (
        "ResourceNotFoundException" in result.stdout
        or "Log group not found" in result.stdout
    )


# --- set-retention-all ---


def test_set_retention_all_success(mock_logs_client):
    """Test set-retention-all with --yes updates all groups."""
    mock_logs_client.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/aws/lambda/a"},
            {"logGroupName": "/aws/lambda/b"},
        ]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention-all", "--days", "30", "--yes"],
    )
    assert result.exit_code == 0
    assert mock_logs_client.put_retention_policy.call_count == 2
    mock_logs_client.put_retention_policy.assert_any_call(
        logGroupName="/aws/lambda/a", retentionInDays=30
    )
    mock_logs_client.put_retention_policy.assert_any_call(
        logGroupName="/aws/lambda/b", retentionInDays=30
    )


def test_set_retention_all_only_unset(mock_logs_client):
    """Test set-retention-all --only-unset updates only groups without retention."""
    mock_logs_client.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/aws/lambda/no-retention"},
            {"logGroupName": "/aws/lambda/has-retention", "retentionInDays": 7},
        ]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention-all", "--days", "30", "--only-unset", "--yes"],
    )
    assert result.exit_code == 0
    mock_logs_client.put_retention_policy.assert_called_once_with(
        logGroupName="/aws/lambda/no-retention", retentionInDays=30
    )


@patch(f"{PATCH_PATH}.typer.confirm")
def test_set_retention_all_confirmation_prompt(mock_confirm, mock_logs_client):
    """Test set-retention-all without --yes calls confirm and aborts on no."""
    mock_confirm.return_value = False
    mock_logs_client.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "g1"}]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention-all", "--days", "30"],
    )
    assert result.exit_code == 0
    assert "Aborted" in result.stdout
    mock_confirm.assert_called_once()
    mock_logs_client.put_retention_policy.assert_not_called()


@patch(f"{PATCH_PATH}.typer.confirm")
def test_set_retention_all_confirmation_proceeds_on_yes(mock_confirm, mock_logs_client):
    """Test set-retention-all without --yes proceeds when user confirms."""
    mock_confirm.return_value = True
    mock_logs_client.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "g1"}]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["set-retention-all", "--days", "30"],
    )
    assert result.exit_code == 0
    mock_confirm.assert_called_once()
    mock_logs_client.put_retention_policy.assert_called_once_with(
        logGroupName="g1", retentionInDays=30
    )


# --- list-streams ---


def test_list_streams_success(mock_logs_client):
    """Test list-streams with sample streams."""
    mock_logs_client.describe_log_streams.return_value = {
        "logStreams": [
            {"logStreamName": "stream-a", "lastEventTimestamp": 1700000000000},
            {"logStreamName": "stream-b", "lastEventTimestamp": 1700000001000},
        ]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["list-streams", "--log-group", "my-group"],
    )
    assert result.exit_code == 0
    assert "stream-a" in result.stdout
    assert "stream-b" in result.stdout
    mock_logs_client.describe_log_streams.assert_called_once_with(
        logGroupName="my-group",
        orderBy="LastEventTime",
        descending=True,
        limit=50,
    )


def test_list_streams_empty(mock_logs_client):
    """Test list-streams when no streams found."""
    mock_logs_client.describe_log_streams.return_value = {"logStreams": []}
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["list-streams", "--log-group", "empty-group"],
    )
    assert result.exit_code == 0
    assert "No log streams found" in result.stdout


# --- read (with stream) ---


def test_read_with_stream_success(mock_logs_client):
    """Test read with --log-group and --log-stream uses get_log_events."""
    mock_logs_client.get_log_events.return_value = {
        "events": [
            {"timestamp": 1700000000000, "message": "first line"},
            {"timestamp": 1700000001000, "message": "second line"},
        ]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["read", "--log-group", "g", "--log-stream", "s", "--limit", "5"],
    )
    assert result.exit_code == 0
    assert "first line" in result.stdout
    assert "second line" in result.stdout
    mock_logs_client.get_log_events.assert_called_once()
    call_kw = mock_logs_client.get_log_events.call_args[1]
    assert call_kw["logGroupName"] == "g"
    assert call_kw["logStreamName"] == "s"
    assert call_kw["limit"] == 5
    mock_logs_client.filter_log_events.assert_not_called()


# --- read (group only) ---


def test_read_group_only_success(mock_logs_client):
    """Test read with only --log-group uses filter_log_events."""
    mock_logs_client.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1700000000000, "message": "event one"},
            {"timestamp": 1700000001000, "message": "event two"},
        ]
    }
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["read", "--log-group", "g", "--limit", "5"],
    )
    assert result.exit_code == 0
    assert "event one" in result.stdout
    assert "event two" in result.stdout
    mock_logs_client.filter_log_events.assert_called_once()
    call_kw = mock_logs_client.filter_log_events.call_args[1]
    assert call_kw["logGroupName"] == "g"
    assert call_kw["limit"] == 5
    mock_logs_client.get_log_events.assert_not_called()


def test_read_group_only_with_filter_pattern(mock_logs_client):
    """Test read with --filter-pattern passes it to filter_log_events."""
    mock_logs_client.filter_log_events.return_value = {"events": []}
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["read", "--log-group", "g", "--filter-pattern", "ERROR"],
    )
    assert result.exit_code == 0
    call_kw = mock_logs_client.filter_log_events.call_args[1]
    assert call_kw["filterPattern"] == "ERROR"


def test_read_no_events(mock_logs_client):
    """Test read when no events returned."""
    mock_logs_client.get_log_events.return_value = {"events": []}
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["read", "--log-group", "g", "--log-stream", "s"],
    )
    assert result.exit_code == 0
    assert "No log events found" in result.stdout


# --- Error handling ---


def test_read_client_error_resource_not_found(mock_logs_client):
    """Test read surfaces ResourceNotFoundException."""
    mock_logs_client.get_log_events.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "Log group not found",
            }
        },
        "get_log_events",
    )
    result = runner.invoke(
        awsbot_cli.commands.logs.app,
        ["read", "--log-group", "missing", "--log-stream", "s"],
    )
    assert result.exit_code == 1
    assert (
        "ResourceNotFoundException" in result.stdout
        or "Log group not found" in result.stdout
    )


def test_list_groups_client_error(mock_logs_client):
    """Test list-groups surfaces AccessDenied."""
    mock_logs_client.describe_log_groups.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
        "describe_log_groups",
    )
    result = runner.invoke(awsbot_cli.commands.logs.app, ["list-groups"])
    assert result.exit_code == 1
    assert "AccessDenied" in result.stdout or "Access denied" in result.stdout


# --- Helper: _parse_since ---


def test_parse_since():
    """Test _parse_since parses 30m, 1h, 2d."""
    # 30m
    start = awsbot_cli.commands.logs._parse_since("30m")
    assert isinstance(start, int)
    assert start > 0
    # 1h
    start_h = awsbot_cli.commands.logs._parse_since("1h")
    assert start_h < start  # 1h ago is earlier than 30m ago
    # 2d
    start_d = awsbot_cli.commands.logs._parse_since("2d")
    assert start_d < start_h


def test_parse_since_invalid_raises():
    """Test _parse_since with invalid input raises BadParameter."""
    with pytest.raises(typer.BadParameter):
        awsbot_cli.commands.logs._parse_since("invalid")
    with pytest.raises(typer.BadParameter):
        awsbot_cli.commands.logs._parse_since("1x")
