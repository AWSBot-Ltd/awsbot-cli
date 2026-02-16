import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from awsbot_cli.workflow.ai_utils import (
    get_gemini_summary,
    get_gemini_labels,
    get_gemini_review,
)


# --- Fixtures ---


@pytest.fixture
def mock_subprocess():
    with patch("subprocess.run") as mock_run:
        yield mock_run


# --- Tests for get_gemini_summary ---


def test_get_gemini_summary_success(mock_subprocess):
    """Verify that summary returns stripped stdout on success."""
    mock_subprocess.return_value = MagicMock(
        returncode=0, stdout="  This is a summary.  ", stderr=""
    )

    result = get_gemini_summary("test prompt")

    assert result == "This is a summary."
    # Verify the CLI was called with the correct model and prompt
    args = mock_subprocess.call_args[0][0]
    assert "gemini" in args
    assert "-p" in args
    assert "Summarize this diff:" in args


def test_get_gemini_summary_failure(mock_subprocess):
    """Verify it returns None if the CLI returns a non-zero exit code."""
    mock_subprocess.return_value = MagicMock(returncode=1, stderr="API Key Error")

    result = get_gemini_summary("test prompt")
    assert result is None


# --- Tests for get_gemini_labels ---


def test_get_gemini_labels_json_cleaning(mock_subprocess):
    """Verify that the utility strips Markdown code blocks before parsing JSON."""
    markdown_json = '```json\n[{"name": "bug", "color": "#FF0000"}]\n```'
    mock_subprocess.return_value = MagicMock(returncode=0, stdout=markdown_json)

    result = get_gemini_labels("diff text")

    assert len(result) == 1
    assert result[0]["name"] == "bug"
    assert result[0]["color"] == "#FF0000"


def test_get_gemini_labels_invalid_json(mock_subprocess):
    """Verify it returns an empty list if Gemini returns garbage."""
    mock_subprocess.return_value = MagicMock(returncode=0, stdout="Not JSON at all")

    result = get_gemini_labels("diff text")
    assert result == []


# --- Tests for get_gemini_review ---


def test_get_gemini_review_parsing(mock_subprocess):
    """Verify structured review data is correctly returned."""
    review_data = [
        {
            "file": "app.py",
            "issue": "Security Risk",
            "comment": "Unsafe input",
            "severity": "High",
        }
    ]
    mock_subprocess.return_value = MagicMock(
        returncode=0, stdout=json.dumps(review_data)
    )

    result = get_gemini_review("diff text")

    assert len(result) == 1
    assert result[0]["issue"] == "Security Risk"
    assert result[0]["severity"] == "High"


def test_get_gemini_review_timeout(mock_subprocess):
    """Verify the function handles a subprocess timeout gracefully."""
    mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="gemini", timeout=180)

    result = get_gemini_review("large diff")
    assert result == []
