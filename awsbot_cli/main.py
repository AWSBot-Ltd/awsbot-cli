#!/usr/bin/env python3
import os
from enum import Enum

import typer

# Import your new command modules
from awsbot_cli.commands import auth, billing, ecr, infra, s3, vpn, workflow
from awsbot_cli.utils.config import load_config
from awsbot_cli.utils.logger import set_log_format

app = typer.Typer(help="AWSBOT CLI Tool")

# Register Subcommands
app.add_typer(billing.app, name="billing")
app.add_typer(s3.app, name="s3")
app.add_typer(infra.app, name="infra")
app.add_typer(workflow.app, name="workflow")
app.add_typer(ecr.app, name="ecr")
app.add_typer(auth.app, name="auth")
app.add_typer(vpn.app, name="vpn")


class LogFormat(str, Enum):
    text = "text"
    json = "json"


@app.callback()
def cli_config(
        ctx: typer.Context,
        profile: str = typer.Option(None, "--profile", "-p", help="Switch context/profile"),
        log_format: str = typer.Option("text", "--log-format", help="Output format"),
):
    """
    Global configuration.
    """
    set_log_format(log_format)

    # 1. Load the Big Config
    full_config = load_config() or {}

    # 2. Determine Active Profile
    # Priority: Flag > Configured Default > "default"
    active_profile_name = profile or full_config.get("active_profile", "default")

    # 3. Get the specific data for this profile
    profile_data = full_config.get("profiles", {}).get(active_profile_name, {})

    # 4. Set Environment Variables for downstream tools

    # --- AWS ---
    # Check if we have a valid cached session for this profile
    cached_session = profile_data.get("cached_session", {})
    if cached_session.get("aws_access_key_id"):
        os.environ["AWS_ACCESS_KEY_ID"] = cached_session["aws_access_key_id"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = cached_session["aws_secret_access_key"]
        os.environ["AWS_SESSION_TOKEN"] = cached_session["aws_session_token"]

    # Set the fallback profile (useful if session is expired or not used)
    if profile_data.get("aws_profile_name"):
        os.environ["AWS_PROFILE"] = profile_data["aws_profile_name"]

    # --- VENDORS (Jira, GitLab, etc) ---
    if profile_data.get("jira_url"):
        os.environ["JIRA_URL"] = profile_data["jira_url"]
        # Assuming you saved a token too
        # os.environ["JIRA_TOKEN"] = profile_data["jira_token"]

    if profile_data.get("gitlab_token"):
        os.environ["GITLAB_TOKEN"] = profile_data["gitlab_token"]

    # Store the active profile name in context in case a command needs to know "who" acts
    ctx.meta["profile_name"] = active_profile_name


def main():
    app()


if __name__ == "__main__":
    main()
