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
    log_format: LogFormat = typer.Option(
        LogFormat.text, "--log-format", help="Output format"
    ),
    profile: str = typer.Option(None, help="AWS profile name to use"),
):
    """
    Global configuration (runs before any command).
    """
    os.environ["NODE_OPTIONS"] = "--no-warnings"
    set_log_format(log_format.value)

    # 5. Load Cached Credentials
    # We only load cached creds if the user isn't trying to run 'auth login' or 'auth configure'
    # otherwise, existing expired creds might block the login attempt.
    invoked_subcommand = ctx.invoked_subcommand

    if invoked_subcommand != "auth":
        config = load_config()
        if config.get("aws_access_key_id"):
            os.environ["AWS_ACCESS_KEY_ID"] = config["aws_access_key_id"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = config["aws_secret_access_key"]
            os.environ["AWS_SESSION_TOKEN"] = config["aws_session_token"]

    # If user manually overrides profile via flag, it takes precedence
    # (though usually with MFA session tokens, you rely on the env vars set above)
    if profile:
        os.environ["AWS_PROFILE"] = profile


def main():
    app()


if __name__ == "__main__":
    main()
