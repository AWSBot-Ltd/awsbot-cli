# auth.py
import os
import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console

# Assuming load_config/save_config can handle nested dicts or you update them to do so
from awsbot_cli.utils.config import load_config, save_full_config

app = typer.Typer(help="Authentication and Credential Management")
console = Console()

from awsbot_cli.utils.config import update_profile


@app.command()
def configure(
        profile: str = typer.Option("default", "--profile", "-p", help="Profile name"),
        # AWS
        aws_profile: str = typer.Option(None, help="Source AWS Profile"),
        mfa_arn: str = typer.Option(None, help="MFA Device ARN"),
        # Vendors - These are the new ones you wanted
        jira_url: str = typer.Option(None, help="Jira Base URL"),
        jira_email: str = typer.Option(None, help="Jira Login Email"),
        jira_token: str = typer.Option(None, help="Jira API Token"),
        gitlab_token: str = typer.Option(None, help="GitLab Personal Access Token"),
        github_token: str = typer.Option(None, help="GitHub Personal Access Token"),
):
    """
    Update credentials for a specific profile context.
    """
    # Create a dictionary of only the values that were actually provided (not None)
    updates = {
        "aws_profile_name": aws_profile,
        "mfa_arn": mfa_arn,
        "jira_url": jira_url,
        "jira_email": jira_email,
        "jira_token": jira_token,
        "gitlab_token": gitlab_token,
        "github_token": github_token,
    }

    # Filter out None values so we don't overwrite existing data with empty values
    clean_updates = {k: v for k, v in updates.items() if v is not None}

    if not clean_updates:
        console.print("[yellow]No changes provided.[/yellow]")
        return

    # Use the new safe update function
    update_profile(profile, **clean_updates)

    console.print(f"[green]Profile '{profile}' updated successfully![/green]")


@app.command()
def login(
        profile: str = typer.Option(
            None, "--profile", "-p", help="The CLI profile to log in with"
        ),
        token_code: str = typer.Option(
            ..., prompt="Enter MFA Token Code", help="The 6-digit code"
        )
):
    """
    Authenticate using MFA for the specified profile.
    """
    config = load_config()

    # Determine which profile to use
    active_profile_name = profile or config.get("active_profile", "default")
    profile_data = config.get("profiles", {}).get(active_profile_name)

    if not profile_data:
        console.print(f"[bold red]Error:[/bold red] Profile '{active_profile_name}' not found.")
        raise typer.Exit(code=1)

    # Extract AWS specific details from that profile
    aws_profile = profile_data.get("aws_profile_name")
    mfa_arn = profile_data.get("mfa_arn")

    if not aws_profile or not mfa_arn:
        console.print("[bold red]Error:[/bold red] AWS details missing in this profile.")
        raise typer.Exit(code=1)

    console.print(f"Authenticating profile [bold blue]{active_profile_name}[/bold blue] (AWS: {aws_profile})...")

    # Clear env vars so boto3 uses the explicit profile
    env_vars_to_clear = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]
    for var in env_vars_to_clear:
        os.environ.pop(var, None)

    try:
        session = boto3.Session(profile_name=aws_profile)
        sts_client = session.client("sts")
        response = sts_client.get_session_token(SerialNumber=mfa_arn, TokenCode=token_code)
        creds = response["Credentials"]

        # Save cached credentials INTO the specific profile
        # This keeps 'client-a' sessions separate from 'client-b' sessions
        profile_data["cached_session"] = {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
        }

        # Save back to main config
        config["profiles"][active_profile_name] = profile_data
        save_full_config(config)

        console.print(f"[green]Success![/green] Session cached for profile '{active_profile_name}'.")

    except Exception as e:
        console.print(f"[bold red]Auth Error:[/bold red] {e}")
        raise typer.Exit(code=1)