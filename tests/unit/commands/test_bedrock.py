"""Unit tests for Bedrock CLI commands."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from typer.testing import CliRunner

import awsbot_cli.commands.bedrock

PATCH_PATH = "awsbot_cli.commands.bedrock"
runner = CliRunner()

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_bedrock_client():
    """Patch get_bedrock_client to return a MagicMock."""
    with patch(f"{PATCH_PATH}.get_bedrock_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_bedrock_runtime_client():
    """Patch get_bedrock_runtime_client to return a MagicMock."""
    with patch(f"{PATCH_PATH}.get_bedrock_runtime_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


def test_list_models_success(mock_bedrock_client):
    """list-models renders table rows from model summaries."""
    mock_bedrock_client.list_foundation_models.return_value = {
        "modelSummaries": [
            {
                "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                "providerName": "Anthropic",
                "modelName": "Claude 3 Haiku",
                "inputModalities": ["TEXT"],
                "outputModalities": ["TEXT"],
                "responseStreamingSupported": True,
                "modelLifecycle": {"status": "ACTIVE"},
            }
        ]
    }
    result = runner.invoke(awsbot_cli.commands.bedrock.app, ["list-models"])
    assert result.exit_code == 0
    assert "anthropic.claude-3-haiku-20240307-v1:0" in result.stdout
    assert "Anthropic" in result.stdout
    assert "ACTIVE" in result.stdout


def test_list_models_empty(mock_bedrock_client):
    """list-models prints empty message when no models returned."""
    mock_bedrock_client.list_foundation_models.return_value = {"modelSummaries": []}
    result = runner.invoke(awsbot_cli.commands.bedrock.app, ["list-models"])
    assert result.exit_code == 0
    assert "No Bedrock models found" in result.stdout


def test_list_models_client_error(mock_bedrock_client):
    """list-models surfaces AWS errors."""
    mock_bedrock_client.list_foundation_models.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
        "list_foundation_models",
    )
    result = runner.invoke(awsbot_cli.commands.bedrock.app, ["list-models"])
    assert result.exit_code == 1
    assert "AccessDeniedException" in result.stdout or "Access denied" in result.stdout


def test_invoke_success_claude(mock_bedrock_runtime_client):
    """invoke prints text content from Claude response."""
    response_payload = {
        "content": [{"type": "text", "text": "Hello from Claude"}],
        "usage": {"input_tokens": 5, "output_tokens": 4},
    }
    mock_bedrock_runtime_client.invoke_model.return_value = {
        "body": io.BytesIO(bytes(json.dumps(response_payload), encoding="utf-8"))
    }
    result = runner.invoke(
        awsbot_cli.commands.bedrock.app,
        [
            "invoke",
            "--model-id",
            "anthropic.claude-3-haiku-20240307-v1:0",
            "--prompt",
            "Say hi",
        ],
    )
    assert result.exit_code == 0
    assert "Hello from Claude" in result.stdout
    mock_bedrock_runtime_client.invoke_model.assert_called_once()


def test_invoke_unsupported_model():
    """invoke exits when model is not Anthropic."""
    result = runner.invoke(
        awsbot_cli.commands.bedrock.app,
        ["invoke", "--model-id", "amazon.titan-text-v1", "--prompt", "hello"],
    )
    assert result.exit_code == 1
    assert "supports Anthropic Claude" in result.stdout


def test_invoke_client_error(mock_bedrock_runtime_client):
    """invoke surfaces AWS runtime errors."""
    mock_bedrock_runtime_client.invoke_model.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Not authorized to invoke",
            }
        },
        "invoke_model",
    )
    result = runner.invoke(
        awsbot_cli.commands.bedrock.app,
        [
            "invoke",
            "--model-id",
            "anthropic.claude-3-haiku-20240307-v1:0",
            "--prompt",
            "Say hi",
        ],
    )
    assert result.exit_code == 1
    assert (
        "AccessDeniedException" in result.stdout
        or "Not authorized to invoke" in result.stdout
    )
