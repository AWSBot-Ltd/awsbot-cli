"""AWS Bedrock CLI: list available foundation models and invoke Claude models."""

import json
import os
import subprocess
from pathlib import Path

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="List Bedrock models and invoke Anthropic Claude")
console = Console()


def _default_region() -> str:
    """Return AWS region from env/session, falling back to us-east-1."""
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if region:
        return region
    try:
        session = boto3.Session()
        if session.region_name:
            return session.region_name
    except Exception:
        pass
    return "us-east-1"


def get_bedrock_client(region: str | None = None):
    """Return Bedrock control-plane client."""
    if region is None:
        region = _default_region()
    return boto3.client("bedrock", region_name=region)


def get_bedrock_runtime_client(region: str | None = None):
    """Return Bedrock runtime client."""
    if region is None:
        region = _default_region()
    return boto3.client("bedrock-runtime", region_name=region)


def _access_status(summary: dict) -> str:
    """Best-effort status text from model metadata."""
    lifecycle = summary.get("modelLifecycle", {})
    status = lifecycle.get("status")
    if status:
        return str(status)
    return "AVAILABLE"


def _format_modalities(values: list[str] | None) -> str:
    """Join modality list safely."""
    if not values:
        return "-"
    return ", ".join(values)


def _extract_claude_text(payload: dict) -> str:
    """Extract user-readable text from Anthropic Bedrock response payload."""
    content = payload.get("content", [])
    text_blocks = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join([part for part in text_blocks if part]).strip()
    if text:
        return text
    return json.dumps(payload, indent=2, default=str)


@app.command("list-models")
def list_models(
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="Optional provider filter, e.g. Anthropic"
    ),
):
    """List Bedrock foundation models in a table."""
    client = get_bedrock_client(region)
    try:
        response = client.list_foundation_models()
        summaries = response.get("modelSummaries", [])

        if provider:
            provider_lower = provider.lower()
            summaries = [
                s
                for s in summaries
                if s.get("providerName", "").lower() == provider_lower
            ]

        if not summaries:
            typer.echo("No Bedrock models found for the current filters.")
            return

        table = Table(title="Bedrock foundation models")
        table.add_column("Model ID", style="cyan")
        table.add_column("Provider")
        table.add_column("Name")
        table.add_column("Input Modalities")
        table.add_column("Output Modalities")
        table.add_column("Streaming")
        table.add_column("Access Status")

        for summary in summaries:
            table.add_row(
                summary.get("modelId", ""),
                summary.get("providerName", ""),
                summary.get("modelName", ""),
                _format_modalities(summary.get("inputModalities")),
                _format_modalities(summary.get("outputModalities")),
                "Yes" if summary.get("responseStreamingSupported") else "No",
                _access_status(summary),
            )

        console.print(table)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        message = error.response.get("Error", {}).get("Message", str(error))
        typer.secho(f"AWS Error ({code}): {message}", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("invoke")
def invoke(
    model_id: str = typer.Option(..., "--model-id", help="Bedrock model ID"),
    prompt: str = typer.Option(..., "--prompt", help="Prompt text to send"),
    max_tokens: int = typer.Option(
        256, "--max-tokens", help="Maximum output tokens for model response"
    ),
    temperature: float = typer.Option(
        0.2, "--temperature", help="Sampling temperature for generation"
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region (default: from env/session or us-east-1)",
    ),
):
    """Invoke an Anthropic Claude model through Bedrock runtime."""
    if not model_id.lower().startswith("anthropic."):
        typer.secho(
            "This command currently supports Anthropic Claude model IDs only.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }

    client = get_bedrock_runtime_client(region)
    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read().decode("utf-8"))
        output_text = _extract_claude_text(payload)
        typer.echo(output_text)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        message = error.response.get("Error", {}).get("Message", str(error))
        if code in {"AccessDeniedException", "UnauthorizedOperation"}:
            hint = "Check IAM/SCP permissions for bedrock:InvokeModel and model access policies."
        elif "use case" in message.lower():
            hint = (
                "Anthropic models may require use-case submission before first access."
            )
        else:
            hint = "Verify region, model ID, and account-level Bedrock permissions."
        typer.secho(f"AWS Error ({code}): {message}", fg=typer.colors.RED)
        typer.secho(hint, fg=typer.colors.YELLOW)
        raise typer.Exit(1)


@app.command("ui")
def ui(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host for open-webui"),
    port: int = typer.Option(8080, "--port", "-p", help="Starting port for open-webui"),
    gateway_port: int = typer.Option(
        8001, "--gateway-port", help="Starting port for Bedrock gateway"
    ),
    indexer_port: int = typer.Option(
        8002, "--indexer-port", help="Starting port for code indexer MCP server"
    ),
    terminal_port: int = typer.Option(
        8003, "--terminal-port", help="Starting port for open-terminal"
    ),
    terminal_api_key: str = typer.Option(
        "terminal", "--terminal-api-key", help="API key for open-terminal"
    ),
    extra_openai_urls: str | None = typer.Option(
        None, "--openai-urls", help="Semicolon-separated extra OpenAI base URLs"
    ),
    extra_openai_keys: str | None = typer.Option(
        None,
        "--openai-keys",
        help="Semicolon-separated API keys matching --openai-urls",
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="AWS region for Bedrock (default: from env/session)",
    ),
):
    """Start the Bedrock gateway, code indexer, open-terminal, and open-webui together."""
    from awsbot_cli.gateway import (
        find_free_port,
        start_gateway,
        wait_for_gateway,
        start_indexer,
        wait_for_indexer,
        start_terminal,
        wait_for_terminal,
        API_KEY,
    )

    try:
        gw_port = find_free_port(gateway_port)
        idx_port = find_free_port(indexer_port)
        term_port = find_free_port(terminal_port)
        ui_port = find_free_port(port)
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)

    # Start gateway
    console.print(f"Starting Bedrock gateway on port {gw_port}...")
    gw_proc = start_gateway(gw_port, region)
    try:
        wait_for_gateway(gw_port, gw_proc)
    except RuntimeError as e:
        gw_proc.terminate()
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1)

    # Start code indexer (best-effort)
    cwd = Path.cwd()
    console.print(f"Starting code indexer on port {idx_port} (indexing {cwd})...")
    idx_proc = start_indexer(cwd, idx_port)
    indexer_ok = wait_for_indexer(idx_port, idx_proc)
    if not indexer_ok:
        console.print(
            "[yellow]Warning:[/] Code indexer did not start (Qdrant running on localhost:6333?)."
            " Continuing without it."
        )

    # Start open-terminal (best-effort)
    console.print(f"Starting open-terminal on port {term_port}...")
    term_proc = start_terminal(term_port, terminal_api_key)
    terminal_ok = wait_for_terminal(term_port, term_proc)
    if not terminal_ok:
        console.print(
            "[yellow]Warning:[/] open-terminal did not start (is it installed? pip install open-terminal)."
            " Continuing without it."
        )

    # Build open-webui env
    base_url = f"http://127.0.0.1:{gw_port}/api/v1"
    indexer_url = f"http://127.0.0.1:{idx_port}/mcp"
    tool_connections = json.dumps(
        [
            {
                "type": "mcp",
                "info": {"id": "code-indexer", "name": "Code Indexer"},
                "config": {"enable": True},
                "url": indexer_url,
            }
        ]
    )

    # Build multi-endpoint OpenAI URLs and keys (Bedrock first, then extras)
    all_base_urls = base_url
    all_api_keys = API_KEY
    if extra_openai_urls:
        all_base_urls += ";" + extra_openai_urls
        all_api_keys += ";" + (extra_openai_keys or "")

    ui_env = {
        **os.environ,
        "OPENAI_API_BASE_URLS": all_base_urls,
        "OPENAI_API_KEYS": all_api_keys,
        "TOOL_SERVER_CONNECTIONS": tool_connections,
    }
    if terminal_ok:
        ui_env["OPEN_TERMINAL_URL"] = f"http://127.0.0.1:{term_port}"
        ui_env["OPEN_TERMINAL_API_KEY"] = terminal_api_key

    console.print(f"Starting open-webui on port {ui_port}...")
    ui_proc = subprocess.Popen(
        ["open-webui", "serve", "--host", host, "--port", str(ui_port)],
        env=ui_env,
    )

    console.print(f"\n[bold green]Bedrock Gateway :[/] http://0.0.0.0:{gw_port}/api/v1")
    if indexer_ok:
        console.print(
            f"[bold green]Code Indexer MCP:[/] http://127.0.0.1:{idx_port}/mcp"
        )
    if terminal_ok:
        console.print(f"[bold green]Open Terminal    :[/] http://127.0.0.1:{term_port}")
    console.print(f"[bold green]Open WebUI       :[/] http://{host}:{ui_port}")
    console.print("\n[bold]Manual config if needed:[/]")
    console.print(f"  OpenAI Base URL : {base_url}   Key: {API_KEY}")
    if extra_openai_urls:
        for url in extra_openai_urls.split(";"):
            console.print(f"  Extra OpenAI    : {url}")
    if indexer_ok:
        console.print(
            f"  MCP Server URL  : {indexer_url}  (Settings → Tools → MCP Servers)"
        )
    if terminal_ok:
        console.print(
            f"  Open Terminal   : http://127.0.0.1:{term_port}  (Integrations → Open Terminal)"
        )
    console.print("\nPress [bold]Ctrl-C[/] to stop all services.\n")

    procs = [p for p in [ui_proc, term_proc, idx_proc, gw_proc] if p is not None]
    try:
        ui_proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
