import json
import pytest
import subprocess
from unittest.mock import MagicMock, patch

# Assuming the class is in awsbot_cli/utils/ssm_handler.py
from awsbot_cli.utils.ssm_handler import SSMConnector


@pytest.fixture
def mock_ssm_client():
    """Fixture to provide a mocked SSM client."""
    return MagicMock()


@pytest.fixture
def mock_boto_session(mock_ssm_client):
    """Fixture to mock the boto3.Session and its client method."""
    with patch("awsbot_cli.utils.ssm_handler.boto3.Session") as mock_session_class:
        mock_session = mock_session_class.return_value
        mock_session.client.return_value = mock_ssm_client
        mock_session.region_name = "us-east-1"
        yield mock_session


@patch("awsbot_cli.utils.ssm_handler.subprocess.check_call")
def test_start_interactive_session_success(
    mock_check_call, mock_ssm_client, mock_boto_session
):
    """Verify that the connector correctly hands off data to the session-manager-plugin."""

    # 1. Setup Mock Response from AWS
    fake_session_response = {
        "SessionId": "test-session-id",
        "TokenValue": "test-token",
        "StreamUrl": "wss://ssm.us-east-1.amazonaws.com",
    }
    mock_ssm_client.start_session.return_value = fake_session_response
    mock_ssm_client.meta.endpoint_url = "https://ssm.us-east-1.amazonaws.com"

    # 2. Execute
    connector = SSMConnector(profile="test-profile")
    connector.start_interactive_session("i-1234567890abcdef0")

    # 3. Assertions
    # Verify Boto3 was called correctly
    mock_ssm_client.start_session.assert_called_once_with(Target="i-1234567890abcdef0")

    # Verify Subprocess (The Plugin) was called with the expected JSON and metadata
    assert mock_check_call.called
    args, _ = mock_check_call.call_args
    cmd_list = args[0]

    assert cmd_list[0] == "session-manager-plugin"
    assert json.loads(cmd_list[1]) == fake_session_response  # Verify session data JSON
    assert cmd_list[2] == "us-east-1"  # Verify region
    assert cmd_list[3] == "StartSession"
    assert "i-1234567890abcdef0" in cmd_list[5]  # Verify Target in JSON params


@patch("awsbot_cli.utils.ssm_handler.sys.exit")
@patch("awsbot_cli.utils.ssm_handler.subprocess.check_call")
def test_start_interactive_session_failure(
    mock_check_call, mock_sys_exit, mock_ssm_client, mock_boto_session
):
    """Verify that failures (like missing plugin) trigger sys.exit(1)."""

    # Simulate the plugin not being installed on the user's machine
    mock_check_call.side_effect = subprocess.CalledProcessError(
        1, "session-manager-plugin"
    )

    connector = SSMConnector()
    connector.start_interactive_session("i-fail")

    # Verify code handles the exception and exits
    mock_sys_exit.assert_called_once_with(1)
