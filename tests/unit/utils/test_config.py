import json
import os
import pytest
from unittest.mock import patch

# Import the module under test
import awsbot_cli.utils.config as config_utils

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_config_path(tmp_path):
    """
    Redirects APP_DIR and CONFIG_FILE to a temporary directory
    for every test to protect real user data.
    """
    temp_app_dir = tmp_path / ".awsbot"
    temp_config_file = temp_app_dir / "config.json"

    with (
        patch("awsbot_cli.utils.config.APP_DIR", temp_app_dir),
        patch("awsbot_cli.utils.config.CONFIG_FILE", temp_config_file),
    ):
        yield temp_config_file


def test_load_config_no_file():
    """Should return default structure if file does not exist."""
    data = config_utils.load_config()
    assert data == {"profiles": {}, "active_profile": "default"}


def test_load_config_invalid_json(mock_config_path):
    """Should return default structure if JSON is corrupted."""
    mock_config_path.parent.mkdir(parents=True, exist_ok=True)
    mock_config_path.write_text("{ invalid json")

    data = config_utils.load_config()
    assert "profiles" in data
    assert data["active_profile"] == "default"


def test_save_full_config(mock_config_path):
    """Verify file is created with correct permissions and content."""
    test_data = {"profiles": {"test": {"key": "val"}}, "active_profile": "test"}
    config_utils.save_full_config(test_data)

    assert mock_config_path.exists()

    # Check content
    with open(mock_config_path, "r") as f:
        saved_data = json.load(f)
    assert saved_data == test_data

    # Check permissions (Unix-like systems: 0o600)
    if os.name != "nt":  # chmod behavior differs on Windows
        mode = os.stat(mock_config_path).st_mode
        assert oct(mode & 0o777) == "0o600"


def test_update_profile_merging(mock_config_path):
    """Verify update_profile merges data instead of overwriting the whole profile."""
    # 1. Initial setup
    config_utils.update_profile("dev", aws_key="123")

    # 2. Update with new key
    config_utils.update_profile("dev", jira_token="abc")

    # 3. Verify both keys exist
    profile = config_utils.get_profile("dev")
    assert profile["aws_key"] == "123"
    assert profile["jira_token"] == "abc"


def test_get_profile_active_fallback(mock_config_path):
    """Verify get_profile falls back to active_profile when None provided."""
    config_utils.save_full_config(
        {
            "profiles": {"prod": {"env": "production"}, "default": {"env": "standard"}},
            "active_profile": "prod",
        }
    )

    # Should get 'prod' data because it's active
    profile = config_utils.get_profile()
    assert profile["env"] == "production"


def test_get_profile_not_found():
    """Should return empty dict if profile doesn't exist."""
    profile = config_utils.get_profile("non-existent")
    assert profile == {}
