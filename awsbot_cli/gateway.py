"""Launch and manage the bedrock gateway and code indexer subprocesses."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API_KEY = "bedrock"
TERMINAL_API_KEY = "terminal"


def find_free_port(start: int, count: int = 20) -> int:
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError(f"No free port found in range {start}–{start + count - 1}")


def start_gateway(port: int, region: str | None = None) -> subprocess.Popen:
    env = {**os.environ, "API_KEY": API_KEY, "API_ROUTE_PREFIX": "/api/v1"}
    if region:
        env["AWS_REGION"] = region
    script = (
        "from awsbot_cli.bedrock_gateway.app import app; "
        "import uvicorn; "
        f"uvicorn.run(app, host='0.0.0.0', port={port})"
    )
    return subprocess.Popen([sys.executable, "-c", script], env=env)


def wait_for_gateway(port: int, proc: subprocess.Popen, timeout: int = 15) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Gateway process exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"Gateway did not become healthy on port {port} within {timeout}s"
    )


def start_indexer(cwd: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "MCP_ALLOWED_ROOTS": str(cwd)}
    script = (
        "from awsbot_cli.code_indexer.server import mcp; "
        f"mcp.run(transport='streamable-http', port={port}, host='127.0.0.1')"
    )
    return subprocess.Popen([sys.executable, "-c", script], cwd=str(cwd), env=env)


def wait_for_indexer(port: int, proc: subprocess.Popen, timeout: int = 15) -> bool:
    """Return True if healthy, False if timed out or process died (non-fatal)."""
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_terminal(port: int, api_key: str = TERMINAL_API_KEY) -> subprocess.Popen:
    """Start open-terminal bare-metal on the given port."""
    return subprocess.Popen(
        [
            "open-terminal",
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--api-key",
            api_key,
        ]
    )


def wait_for_terminal(port: int, proc: subprocess.Popen, timeout: int = 15) -> bool:
    """Return True when /health responds 200, False on timeout/early exit."""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False
