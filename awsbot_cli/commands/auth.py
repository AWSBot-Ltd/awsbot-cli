import os

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console

from awsbot_cli.utils.config import load_config, save_config

app = typer.Typer(help="Authentication and Credential Management")
console = Console()


@app.command()
def configure(
    mfa_arn: str = typer.Option(
        ..., prompt="Enter your MFA Device ARN", help="The ARN of your MFA device"
    ),
    profile: str = typer.Option(
        "default",
        prompt="Enter AWS Profile name",
        help="The long-term AWS profile to use",
    ),
):
    """
    Store MFA ARN and Profile configuration for future use.
    """
    save_config({"mfa_arn": mfa_arn, "base_aws_profile": profile})
    console.print(
        f"[green]Configuration saved![/green] (Profile: {profile}, MFA: {mfa_arn})"
    )


@app.command()
def login(
    token_code: str = typer.Option(
        ..., prompt="Enter MFA Token Code", help="The 6-digit code from your MFA device"
    )
):
    """
    Authenticate using MFA and cache session credentials.
    """
    config = load_config()
    profile = config.get("base_aws_profile")
    mfa_arn = config.get("mfa_arn")

    if not profile or not mfa_arn:
        console.print(
            "[bold red]Error:[/bold red] Configuration missing. Please run `awsbot-cli auth configure` first."
        )
        raise typer.Exit(code=1)

    console.print(f"Authenticating with profile [bold blue]{profile}[/bold blue]...")

    # Important: Ensure we don't use stale env vars for the STS call itself
    # We want to use the long-term profile credentials to get the session token
    env_vars_to_clear = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ]
    auth_env = os.environ.copy()
    for var in env_vars_to_clear:
        auth_env.pop(var, None)

    try:
        # Create a session using the base profile
        session = boto3.Session(profile_name=profile)
        sts_client = session.client("sts")

        response = sts_client.get_session_token(
            SerialNumber=mfa_arn, TokenCode=token_code
        )

        creds = response["Credentials"]

        # Save the temporary credentials
        save_config(
            {
                "aws_access_key_id": creds["AccessKeyId"],
                "aws_secret_access_key": creds["SecretAccessKey"],
                "aws_session_token": creds["SessionToken"],
            }
        )

        console.print(
            "[green]Success![/green] Session credentials cached. You can now run other commands."
        )

    except ClientError as e:
        console.print(f"[bold red]Authentication Failed:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]Unexpected Error:[/bold red] {e}")
        raise typer.Exit(code=1)
