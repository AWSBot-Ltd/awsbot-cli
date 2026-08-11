"""Configure agent settings per profile."""

import typer
from rich.console import Console
from rich.table import Table

from awsbot_cli.utils.config import get_profile, update_profile

console = Console()

app = typer.Typer(help="Configure agent provider settings per profile")


@app.command("set")
def set_config(
    ctx: typer.Context,
    provider: str = typer.Option(
        None,
        "--provider",
        "-p",
        help="LLM provider (claude, bedrock, openai, gemini, deepseek, ollama)",
    ),
    model: str = typer.Option(None, "--model", "-m", help="Model ID/name"),
    region: str = typer.Option(None, "--region", "-r", help="AWS region (for Bedrock)"),
    timeout: int = typer.Option(
        None, "--timeout", "-t", help="Command timeout in seconds"
    ),
    profile: str = typer.Option(
        None, "--profile", "-P", help="Profile name (default: active)"
    ),
):
    """
    Set agent configuration for a profile.

    Example:
        awsbot-cli agent-config set --provider bedrock --model anthropic.claude-3-5-sonnet-20241022-v2:0 --region us-east-1
    """
    # Determine which profile to update
    profile_name = profile or ctx.meta.get("profile_name", "default")

    # Build the agent config dict
    agent_config = {}
    if provider:
        agent_config["provider"] = provider
    if model:
        agent_config["model"] = model
    if region:
        agent_config["region"] = region
    if timeout is not None:
        agent_config["timeout"] = timeout

    if not agent_config:
        console.print(
            "[yellow]No settings specified. Use --provider, --model, --region, or --timeout.[/yellow]"
        )
        return

    # Update the profile
    try:
        update_profile(profile_name, agent=agent_config)
        console.print(
            f"[green]✓ Agent settings updated for profile '{profile_name}'[/green]"
        )
        _show_profile_config(profile_name)
    except Exception as e:
        console.print(f"[red]Error updating profile: {e}[/red]")
        raise typer.Exit(1)


@app.command("show")
def show_config(
    ctx: typer.Context,
    profile: str = typer.Option(
        None, "--profile", "-P", help="Profile name (default: active)"
    ),
):
    """
    Show agent configuration for a profile.

    Example:
        awsbot-cli agent-config show
        awsbot-cli agent-config show --profile my-profile
    """
    profile_name = profile or ctx.meta.get("profile_name", "default")
    _show_profile_config(profile_name)


def _show_profile_config(profile_name: str):
    """Display agent config for a given profile."""
    profile = get_profile(profile_name)

    if not profile:
        console.print(f"[yellow]Profile '{profile_name}' not found.[/yellow]")
        return

    agent_config = profile.get("agent", {})

    if not agent_config:
        console.print(
            f"[yellow]No agent configuration for profile '{profile_name}'.[/yellow]"
        )
        return

    # Display as table
    table = Table(title=f"Agent Configuration - Profile '{profile_name}'")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Provider", agent_config.get("provider", "(not set)"))
    table.add_row("Model", agent_config.get("model", "(not set)"))
    table.add_row("Region", agent_config.get("region", "(not set)"))
    table.add_row("Timeout (s)", str(agent_config.get("timeout", "(not set)")))

    console.print(table)
