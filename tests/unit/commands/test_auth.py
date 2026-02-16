import pytest
import re
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

# --- IMPORT YOUR APP ---
# Verify this import matches your file structure.
# If auth.py is in awsbot_cli/commands/auth.py, import it from there.
from awsbot_cli.commands.auth import app

# --- CONFIGURATION ---
# IMPORTANT: Change this to the full dotted path of your auth.py file.
# If your project is `awsbot_cli` and file is `auth.py`, this might be:
# "awsbot_cli.auth" or "awsbot_cli.commands.auth"
PATCH_PATH = "awsbot_cli.commands.auth"

runner = CliRunner()


# --- HELPER ---
def strip_ansi(text):
    """Removes ANSI color codes from Rich/Typer output for easy assertion."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


# --- TEST DATA ---
MOCK_CONFIG = {
    "active_profile": "default",
    "profiles": {
        "test-user": {
            "aws_profile_name": "source-aws-profile",
            "mfa_arn": "arn:aws:iam::123456789012:mfa/user",
            "cached_session": {},
        }
    },
}

MOCK_CREDS = {
    "Credentials": {
        "AccessKeyId": "ASIA_TEST",
        "SecretAccessKey": "SECRET_TEST",
        "SessionToken": "TOKEN_TEST",
    }
}


# --- TESTS ---


@pytest.mark.unit
@pytest.mark.configure
def test_configure_updates_profile_successfully():
    # Fix: Use PATCH_PATH
    with patch(f"{PATCH_PATH}.update_profile") as mock_update:
        result = runner.invoke(
            app,
            [
                "configure",
                "--profile",
                "test-user",
                "--aws-profile",
                "my-aws-prof",
                "--jira-email",
                "me@company.com",
            ],
        )

        assert result.exit_code == 0

        # Fix: Strip ANSI codes before asserting
        clean_output = strip_ansi(result.stdout)
        assert "Profile 'test-user' updated successfully" in clean_output

        mock_update.assert_called_once_with(
            "test-user", aws_profile_name="my-aws-prof", jira_email="me@company.com"
        )


@pytest.mark.unit
@pytest.mark.login
def test_login_success_flow():
    # Fix: Use PATCH_PATH for all mocks
    with (
        patch(f"{PATCH_PATH}.load_config", return_value=MOCK_CONFIG),
        patch(f"{PATCH_PATH}.save_full_config") as mock_save,
        patch("boto3.Session") as mock_boto_session,
    ):
        mock_sts = MagicMock()
        mock_sts.get_session_token.return_value = MOCK_CREDS

        mock_session_instance = mock_boto_session.return_value
        mock_session_instance.client.return_value = mock_sts

        result = runner.invoke(
            app, ["login", "--profile", "test-user", "--token-code", "123456"]
        )

        assert result.exit_code == 0

        # Fix: Strip ANSI codes
        assert "Success!" in strip_ansi(result.stdout)

        # Verify logic
        saved_config = mock_save.call_args[0][0]
        cached = saved_config["profiles"]["test-user"]["cached_session"]
        assert cached["aws_access_key_id"] == "ASIA_TEST"


@pytest.mark.unit
@pytest.mark.login
def test_login_fails_if_profile_missing():
    empty_config = {"profiles": {}}

    with patch(f"{PATCH_PATH}.load_config", return_value=empty_config):
        result = runner.invoke(
            app, ["login", "--profile", "ghost-user", "--token-code", "000000"]
        )

        assert result.exit_code == 1

        # Fix: Strip ANSI codes so "Profile 'ghost-user' not found" matches
        clean_output = strip_ansi(result.stdout)
        assert "Profile 'ghost-user' not found" in clean_output
