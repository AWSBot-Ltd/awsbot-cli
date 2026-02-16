import pytest
from unittest.mock import MagicMock, patch
import gitlab

from awsbot_cli.workflow.gitlab_utils import (
    ensure_label_with_color,
    run_command,
    get_project_path_from_git,
    update_gitlab_mr,
    post_gemini_review,
)


# --- Fixtures ---


@pytest.fixture
def mock_project():
    return MagicMock()


@pytest.fixture
def mock_mr():
    mr = MagicMock()
    mr.web_url = "https://gitlab.com/test/project/-/merge_requests/1"
    mr.labels = []
    return mr


# --- Tests ---


def test_ensure_label_exists(mock_project):
    """Should not try to create a label if it already exists."""
    # Setup: .get() succeeds
    mock_project.labels.get.return_value = MagicMock()

    ensure_label_with_color(mock_project, "bug", "#FF0000")

    mock_project.labels.get.assert_called_once_with("bug")
    mock_project.labels.create.assert_not_called()


def test_ensure_label_creates_if_missing(mock_project):
    """Should call .create() if .get() raises GitlabGetError."""
    # Setup: .get() fails with 404
    mock_project.labels.get.side_effect = gitlab.exceptions.GitlabGetError(
        response_code=404
    )

    ensure_label_with_color(mock_project, "new-label", "#123456")

    mock_project.labels.create.assert_called_once_with(
        {"name": "new-label", "color": "#123456"}
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@gitlab.com:org/sub/repo.git", "org/sub/repo"),
        ("https://gitlab.com/org/repo.git", "org/repo"),
        ("https://gitlab.com/group/subgroup/project", "group/subgroup/project"),
        ("invalid-url", None),
    ],
)
def test_get_project_path_from_git(url, expected):
    """Verify regex-like splitting logic for different git remote formats."""
    with patch("subprocess.check_output") as mock_git:
        mock_git.return_value = url
        assert get_project_path_from_git() == expected


def test_run_command_success():
    """Verify run_command captures stdout."""
    with patch("subprocess.Popen") as mock_popen:
        process = mock_popen.return_value
        process.communicate.return_value = ("hello world", "")
        process.returncode = 0

        result = run_command("echo hello")
        assert result == "hello world"


@patch("awsbot_cli.workflow.gitlab_utils.gitlab.Gitlab")
@patch("os.getenv")
@patch("awsbot_cli.workflow.gitlab_utils.get_project_path_from_git")
def test_update_gitlab_mr_success(
    mock_get_path, mock_env, mock_gitlab_class, mock_mr, mock_project
):
    """Test full flow of updating an MR with labels and description."""
    # Mocking environment and path
    mock_env.return_value = "fake-token"
    mock_get_path.return_value = "org/repo"

    # Mocking GitLab Hierarchy: gl.projects.get -> project.mergerequests.list -> [mr]
    gl_instance = mock_gitlab_class.return_value
    gl_instance.projects.get.return_value = mock_project
    mock_project.mergerequests.list.return_value = [mock_mr]

    # Define input labels
    labels = [{"name": "AI-Reviewed", "color": "#00FF00"}]

    result = update_gitlab_mr("feature-branch", "Summary of changes", labels=labels)

    assert result is True
    assert "Summary of changes" in mock_mr.description
    assert mock_mr.save.called
    assert mock_mr.labels == ["AI-Reviewed"]


@patch("awsbot_cli.workflow.gitlab_utils.gitlab.Gitlab")
@patch("os.getenv")
@patch("awsbot_cli.workflow.gitlab_utils.get_project_path_from_git")
def test_post_gemini_review_table_format(
    mock_get_path, mock_env, mock_gitlab_class, mock_mr, mock_project
):
    """Verify that review data is correctly formatted into a Markdown table."""
    mock_env.return_value = "fake-token"
    mock_get_path.return_value = "org/repo"

    gl_instance = mock_gitlab_class.return_value
    gl_instance.projects.get.return_value = mock_project
    mock_project.mergerequests.list.return_value = [mock_mr]

    review_data = [
        {
            "severity": "High",
            "file": "app.py",
            "issue": "Security",
            "comment": "Fix this",
        }
    ]

    result = post_gemini_review("feature-branch", review_data)

    assert result is True
    # Verify the note was created
    mock_mr.notes.create.assert_called_once()
    posted_body = mock_mr.notes.create.call_args[0][0]["body"]

    assert "## 🤖 Gemini AI Code Review" in posted_body
    assert "🔴 High" in posted_body
    assert "`app.py`" in posted_body
