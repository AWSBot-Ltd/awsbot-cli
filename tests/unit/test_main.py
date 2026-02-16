import os
import pytest
from typer.testing import CliRunner
from unittest.mock import patch
from awsbot_cli.main import app

runner = CliRunner()


@pytest.fixture
def mock_config():
    """Returns a dummy configuration structure."""
    return {
        "active_profile": "default",
        "profiles": {
            "default": {
                "aws_profile_name": "my-aws-default",
                "gitlab_token": "gl-default-123",
            },
            "prod": {
                "aws_profile_name": "my-aws-prod",
                "gitlab_token": "gl-prod-456",
                "jira_url": "https://jira.prod.com",
                "cached_session": {
                    "aws_access_key_id": "AKIA_PROD",
                    "aws_secret_access_key": "SEC_PROD",
                    "aws_session_token": "TOK_PROD",
                },
            },
        },
    }


def test_subcommands_registered():
    """Verify all main subcommands are visible in the help output."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Check for a few key subcommands
    assert "billing" in result.stdout
    assert "workflow" in result.stdout
    assert "infra" in result.stdout
    assert "auth" in result.stdout


@patch("awsbot_cli.main.load_config")
@patch("awsbot_cli.main.set_log_format")
def test_global_callback_profile_loading(mock_log, mock_load, mock_config):
    """Verify that environment variables are correctly set based on the profile."""
    mock_load.return_value = mock_config

    # We use a dummy command (e.g., 'auth') just to trigger the callback
    # Typer callbacks run before the subcommand
    with patch.dict(os.environ, {}, clear=True):
        runner.invoke(app, ["--profile", "prod", "auth", "--help"])

        # Verify AWS Session variables (from cached_session)
        assert os.environ.get("AWS_ACCESS_KEY_ID") == "AKIA_PROD"
        assert os.environ.get("AWS_SESSION_TOKEN") == "TOK_PROD"

        # Verify Vendor variables
        assert os.environ.get("GITLAB_TOKEN") == "gl-prod-456"
        assert os.environ.get("JIRA_URL") == "https://jira.prod.com"


@patch("awsbot_cli.main.load_config")
def test_active_profile_priority(mock_load, mock_config):
    """Ensure Flag priority > Config Default."""
    mock_load.return_value = mock_config

    # CASE 1: No flag provided - should use 'default' from mock_config
    with patch.dict(os.environ, {}, clear=True):
        runner.invoke(app, ["auth", "--help"])
        assert os.environ.get("GITLAB_TOKEN") == "gl-default-123"

    # CASE 2: Flag provided - should override 'default'
    with patch.dict(os.environ, {}, clear=True):
        runner.invoke(app, ["--profile", "prod", "auth", "--help"])
        assert os.environ.get("GITLAB_TOKEN") == "gl-prod-456"


@patch("awsbot_cli.main.set_log_format")
def test_log_format_callback(mock_log_format):
    """Verify the log format global option calls the utility function."""
    runner.invoke(app, ["--log-format", "json", "auth", "--help"])
    mock_log_format.assert_called_once_with("json")


def test_main_invocation():
    """Smoke test for the main() entry point."""
    from awsbot_cli.main import main

    with patch("awsbot_cli.main.app") as mock_app:
        main()
        mock_app.assert_called_once()
