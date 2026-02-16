import os
import re
from unittest.mock import MagicMock, patch, ANY  # <--- Imported ANY
import pytest
from typer.testing import CliRunner

# Import your module
import awsbot_cli.commands.github

runner = CliRunner()


def strip_ansi(text):
    """Removes ANSI escape codes (colors/bold) from Rich output."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


# --- Fixtures ---


@pytest.fixture
def mock_env_token():
    """Sets a fake GITHUB_TOKEN in environment variables."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token-123"}):
        yield


@pytest.fixture
def mock_requests():
    """Patches the requests library."""
    # PATCHING THE CORRECT PATH
    with patch("awsbot_cli.commands.github.requests") as mock_req:
        yield mock_req


# --- Helper Tests ---


def test_get_headers_missing_token():
    """Test that missing token raises an exit."""
    with patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(
            awsbot_cli.commands.github.app,
            ["issue-create", "--repo", "r", "--org", "o", "--title", "t"],
        )
        assert result.exit_code == 1
        # Strip ANSI to ensure we match the text regardless of color
        clean_output = strip_ansi(result.stdout)
        assert "GITHUB_TOKEN not set" in clean_output


def test_get_headers_success(mock_env_token):
    """Test headers are generated correctly."""
    headers = awsbot_cli.commands.github.get_headers()
    assert headers["Authorization"] == "token fake-token-123"
    assert headers["Accept"] == "application/vnd.github.v3+json"


# --- Command Tests ---


def test_create_issue(mock_env_token, mock_requests):
    """Test creating an issue."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "html_url": "http://github.com/org/repo/issues/1"
    }
    mock_requests.post.return_value = mock_response

    result = runner.invoke(
        awsbot_cli.commands.github.app,
        [
            "issue-create",
            "--repo",
            "my-repo",
            "--org",
            "my-org",
            "--title",
            "Bug Fix",
            "--assignee",
            "dev-user",
        ],
    )

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)
    assert "Success!" in clean_output
    assert "http://github.com/org/repo/issues/1" in clean_output

    mock_requests.post.assert_called_once()
    args, kwargs = mock_requests.post.call_args
    assert args[0] == "https://api.github.com/repos/my-org/my-repo/issues"
    assert kwargs["json"]["title"] == "Bug Fix"


def test_update_pr_state_and_comment(mock_env_token, mock_requests):
    """Test updating PR state and adding a comment."""
    mock_patch_resp = MagicMock()
    mock_patch_resp.status_code = 200
    mock_requests.patch.return_value = mock_patch_resp

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_requests.post.return_value = mock_post_resp

    result = runner.invoke(
        awsbot_cli.commands.github.app,
        [
            "pr-update",
            "101",
            "--repo",
            "core-api",
            "--state",
            "closed",
            "--comment",
            "Closing as stale.",
        ],
    )

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)

    # Assert against clean text
    assert "state updated to 'closed'" in clean_output
    assert "Comment added" in clean_output

    mock_requests.patch.assert_called_once()
    assert mock_requests.patch.call_args[1]["json"] == {"state": "closed"}

    mock_requests.post.assert_called_once()
    assert mock_requests.post.call_args[1]["json"] == {"body": "Closing as stale."}


def test_audit_repos_dry_run(mock_env_token, mock_requests):
    """Test audit in dry run mode."""
    mock_requests.get.side_effect = [
        MagicMock(
            json=lambda: [
                {"name": "fork-repo", "fork": True},
                {"name": "public-repo", "fork": False},
            ]
        ),
        MagicMock(json=lambda: []),
    ]

    result = runner.invoke(
        awsbot_cli.commands.github.app, ["audit-repos", "--org", "test-org"]
    )

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)

    assert "Scanning test-org" in clean_output
    assert "Found Fork (Dry Run): fork-repo" in clean_output
    assert "Found Public Repo (Dry Run): public-repo" in clean_output

    mock_requests.delete.assert_not_called()


def test_audit_repos_fix_mode(mock_env_token, mock_requests):
    """Test audit in FIX mode."""
    mock_requests.get.side_effect = [
        MagicMock(
            json=lambda: [
                {"name": "fork-repo", "fork": True},
                {"name": "public-repo", "fork": False},
            ]
        ),
        MagicMock(json=lambda: []),
    ]

    result = runner.invoke(
        awsbot_cli.commands.github.app, ["audit-repos", "--org", "test-org", "--fix"]
    )

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)

    assert "Deleting Fork: fork-repo" in clean_output
    assert "Making Private: public-repo" in clean_output

    # --- FIX: Use ANY instead of pytest.any_dict ---
    mock_requests.delete.assert_called_with(
        "https://api.github.com/repos/test-org/fork-repo", headers=ANY
    )
    mock_requests.patch.assert_called_with(
        "https://api.github.com/repos/test-org/public-repo",
        json={"private": True},
        headers=ANY,
    )


def test_transfer_all(mock_env_token, mock_requests):
    """Test bulk transferring repositories."""
    mock_requests.get.return_value = MagicMock(
        json=lambda: [
            {"name": "repo-A", "owner": {"login": "my-user"}},
            {"name": "repo-B", "owner": {"login": "other-user"}},
        ]
    )
    mock_requests.post.return_value = MagicMock(status_code=202)

    result = runner.invoke(
        awsbot_cli.commands.github.app,
        ["transfer-all", "dest-org", "--source-user", "my-user"],
    )

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)

    assert "Transferring repo-A" in clean_output
    assert "Transferring repo-B" not in clean_output

    # --- FIX: Use ANY here as well ---
    mock_requests.post.assert_called_once_with(
        "https://api.github.com/repos/my-user/repo-A/transfer",
        headers=ANY,
        json={"new_owner": "dest-org"},
    )


def test_transfer_all_no_repos(mock_env_token, mock_requests):
    """Test handling of empty repo list."""
    mock_requests.get.return_value = MagicMock(json=lambda: [])
    result = runner.invoke(awsbot_cli.commands.github.app, ["transfer-all", "dest-org"])

    assert result.exit_code == 0
    clean_output = strip_ansi(result.stdout)
    assert "No repositories found" in clean_output
