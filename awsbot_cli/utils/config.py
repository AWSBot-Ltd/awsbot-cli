import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Define where to store the config
APP_DIR = Path.home() / ".awsbot"
CONFIG_FILE = APP_DIR / "config.json"


def load_config() -> Dict[str, Any]:
    """Load the full configuration from the JSON file."""
    if not CONFIG_FILE.exists():
        return {"profiles": {}, "active_profile": "default"}
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            # Ensure basic structure exists if file is empty or old format
            if "profiles" not in data:
                data["profiles"] = {}
            return data
    except json.JSONDecodeError:
        return {"profiles": {}, "active_profile": "default"}


def save_full_config(data: Dict[str, Any]) -> None:
    """
    Overwrites the entire config file.
    Internal use only; prefer update_profile() for safety.
    """
    APP_DIR.mkdir(parents=True, exist_ok=True)

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

    # Set file permissions to be readable only by the user (600)
    os.chmod(CONFIG_FILE, 0o600)


def update_profile(profile_name: str, **kwargs) -> None:
    """
    Safely updates specific keys for a single profile.

    Usage:
        update_profile("client-a", jira_url="...", gitlab_token="...")
    """
    config = load_config()

    # 1. Get or create the profile dict
    if profile_name not in config["profiles"]:
        config["profiles"][profile_name] = {}

    # 2. Merge new values into that profile
    # This ensures we don't wipe out existing AWS keys when adding a Jira token
    current_profile_data = config["profiles"][profile_name]
    current_profile_data.update(kwargs)

    # 3. Save back to the main config object
    config["profiles"][profile_name] = current_profile_data

    # 4. Write to disk
    save_full_config(config)


def get_profile(profile_name: str = None) -> Dict[str, Any]:
    """
    Retrieve data for a specific profile (or the active one if None).
    """
    config = load_config()

    if not profile_name:
        profile_name = config.get("active_profile", "default")

    return config.get("profiles", {}).get(profile_name, {})