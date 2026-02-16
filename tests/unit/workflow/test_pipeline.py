import pytest
from unittest.mock import patch, mock_open
from pathlib import Path
from awsbot_cli.workflow.pipeline import find_template, get_jira_id, run_ai_pipeline

pytestmark = pytest.mark.unit

# --- Tests for find_template ---


def test_find_template_finds_package_path():
    """Verify it prioritizes the package template if it exists."""
    with patch("awsbot_cli.workflow.pipeline.Path.exists") as mock_exists:
        mock_exists.return_value = True
        result = find_template()
        assert isinstance(result, Path)
        assert "merge_request_templates" in str(result)


# --- Tests for get_jira_id ---


@pytest.mark.parametrize(
    "branch_name, expected_id",
    [
        ("feature/STS-1234-add-logging", "STS-1234"),
        ("fix/STS-9999-crash", "STS-9999"),
        ("no-jira-id-here", None),
        ("STS-123-too-short", None),  # Assuming 4 digits based on your regex
    ],
)
def test_get_jira_id(branch_name, expected_id):
    assert get_jira_id(branch_name) == expected_id


# --- Tests for run_ai_pipeline ---


@patch("awsbot_cli.workflow.pipeline.run_command")
@patch("awsbot_cli.workflow.pipeline.find_template")
@patch("awsbot_cli.workflow.pipeline.get_gemini_summary")
@patch("awsbot_cli.workflow.pipeline.get_gemini_labels")
@patch("awsbot_cli.workflow.pipeline.update_gitlab_mr")
@patch("awsbot_cli.workflow.pipeline.update_jira_issue")
@patch("awsbot_cli.workflow.pipeline.post_gemini_review")
@patch("awsbot_cli.workflow.pipeline.get_gemini_review")
def test_run_ai_pipeline_full_flow(
    mock_get_review,
    mock_post_review,
    mock_update_jira,
    mock_update_gitlab,
    mock_get_labels,
    mock_get_summary,
    mock_find_template,
    mock_run_cmd,
):
    """
    Ensures that the pipeline orchestrates calls between
    Git, AI, GitLab, and Jira correctly.
    """
    # 1. Setup Mocks
    mock_run_cmd.side_effect = ["feature/STS-1234-test", "fake-diff-content"]
    mock_find_template.return_value = Path("dummy_template.md")
    mock_get_summary.return_value = "AI Generated Summary"
    mock_get_labels.return_value = [{"name": "logic", "color": "#000000"}]
    mock_get_review.return_value = [{"issue": "typo"}]

    # Mock reading the template file
    with patch("builtins.open", mock_open(read_data="Template Content")):
        # 2. Execute
        run_ai_pipeline(update_mr=True, update_jira=True, review=True)

    # 3. Assertions
    # Verify Git calls
    assert mock_run_cmd.call_count == 2

    # Verify AI Summary was requested with the right context
    mock_get_summary.assert_called_once()
    args, _ = mock_get_summary.call_args
    assert "fake-diff-content" in args[0]
    assert "Template Content" in args[0]

    # Verify Review was triggered
    mock_get_review.assert_called_once_with("fake-diff-content")
    mock_post_review.assert_called_once_with(
        "feature/STS-1234-test", [{"issue": "typo"}]
    )

    # Verify Platform updates
    mock_update_gitlab.assert_called_once_with(
        "feature/STS-1234-test",
        "AI Generated Summary",
        labels=[{"name": "logic", "color": "#000000"}],
    )
    mock_update_jira.assert_called_once_with("STS-1234", "AI Generated Summary")


def test_run_ai_pipeline_no_git():
    """Verify pipeline exits early if not in a git repo."""
    with patch("awsbot_cli.workflow.pipeline.run_command") as mock_run:
        mock_run.return_value = None  # Simulates git command failure

        # This should not raise an error, just print and return
        run_ai_pipeline(True, True)

        assert mock_run.called
