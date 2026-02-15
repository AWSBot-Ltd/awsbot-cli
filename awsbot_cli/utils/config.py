import json
import os
from pathlib import Path
from typing import Any, Dict

# Define where to store the config
APP_DIR = Path.home() / ".awsbot"
CONFIG_FILE = APP_DIR / "config.json"


def load_config() -> Dict[str, Any]:
    """Load the configuration from the JSON file."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_config(data: Dict[str, Any]) -> None:
    """Update and save configuration to the JSON file securely."""
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing config to ensure we don't overwrite other keys
    current_config = load_config()
    current_config.update(data)

    with open(CONFIG_FILE, "w") as f:
        json.dump(current_config, f, indent=4)

    # Set file permissions to be readable only by the user (600)
    os.chmod(CONFIG_FILE, 0o600)
