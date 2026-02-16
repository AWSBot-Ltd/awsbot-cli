import json
import datetime
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

# Import your module here (assuming it is saved as ecr.py)
import awsbot_cli.commands.ecr

PATCH_PATH = "awsbot_cli.commands.ecr"
runner = CliRunner()

pytestmark = pytest.mark.unit

# --- Fixtures ---


@pytest.fixture
def mock_ecr_client():
    """
    Patches the get_ecr_client function to return a MagicMock.
    """
    with patch(f"{PATCH_PATH}.get_ecr_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        yield mock_client


# --- Unit Tests for Helper Functions ---


def test_generate_policy_structure():
    """Test that the IAM policy JSON is generated correctly."""
    push_arns = ["arn:aws:iam::123:role/PushRole"]
    pull_arns = ["arn:aws:iam::123:role/PullRole"]

    policy_json = awsbot_cli.commands.ecr.generate_policy(push_arns, pull_arns)
    policy = json.loads(policy_json)

    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 2

    # Check Pull statement
    pull_stmt = next(s for s in policy["Statement"] if s["Sid"] == "AllowPull")
    assert pull_stmt["Principal"]["AWS"] == pull_arns
    assert "ecr:GetDownloadUrlForLayer" in pull_stmt["Action"]

    # Check Push statement
    push_stmt = next(s for s in policy["Statement"] if s["Sid"] == "AllowPushPull")
    assert push_stmt["Principal"]["AWS"] == push_arns
    assert "ecr:PutImage" in push_stmt["Action"]


def test_generate_policy_none():
    """Test that it returns None if no ARNs are provided."""
    assert awsbot_cli.commands.ecr.generate_policy([], []) is None


# --- Integration Tests for CLI Commands ---


def test_create_repo_success(mock_ecr_client):
    """Test creating a repo successfully."""
    result = runner.invoke(awsbot_cli.commands.ecr.app, ["create", "my-repo"])

    assert result.exit_code == 0
    assert "Created repository: my-repo" in result.stdout

    mock_ecr_client.create_repository.assert_called_once_with(
        repositoryName="my-repo",
        imageScanningConfiguration={"scanOnPush": True},  # Default
    )


def test_create_repo_with_policy(mock_ecr_client):
    """Test creating a repo and applying a policy."""
    result = runner.invoke(
        awsbot_cli.commands.ecr.app,
        ["create", "my-repo", "--allow-push", "arn:aws:iam::1:user/dev"],
    )

    assert result.exit_code == 0
    assert "Permissions applied successfully" in result.stdout

    mock_ecr_client.set_repository_policy.assert_called_once()
    call_args = mock_ecr_client.set_repository_policy.call_args[1]
    assert "arn:aws:iam::1:user/dev" in call_args["policyText"]


def test_grant_permission(mock_ecr_client):
    """Test granting specific permissions."""
    result = runner.invoke(
        awsbot_cli.commands.ecr.app,
        ["grant", "my-repo", "arn:aws:iam::1:user/ci", "--access", "push"],
    )

    assert result.exit_code == 0
    assert "Granted push access" in result.stdout

    # Verify we generated a policy with Push access
    mock_ecr_client.set_repository_policy.assert_called_once()
    policy = json.loads(
        mock_ecr_client.set_repository_policy.call_args[1]["policyText"]
    )
    assert policy["Statement"][0]["Sid"] == "AllowPushPull"


def test_delete_repo(mock_ecr_client):
    """Test deleting a repo."""
    result = runner.invoke(
        awsbot_cli.commands.ecr.app, ["delete", "old-repo", "--force"]
    )

    assert result.exit_code == 0
    assert "Deleted repository: old-repo" in result.stdout

    mock_ecr_client.delete_repository.assert_called_once_with(
        repositoryName="old-repo", force=True
    )


def test_list_repos(mock_ecr_client):
    """Test listing repos with pagination."""
    # Mock pagination
    paginator = MagicMock()
    mock_ecr_client.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {"repositories": [{"repositoryName": "repo1", "repositoryUri": "uri1"}]},
        {"repositories": [{"repositoryName": "repo2", "repositoryUri": "uri2"}]},
    ]

    result = runner.invoke(awsbot_cli.commands.ecr.app, ["list"])

    assert result.exit_code == 0
    assert "repo1 (uri1)" in result.stdout
    assert "repo2 (uri2)" in result.stdout


# --- Complex Logic Test: Cleanup Images ---


def test_cleanup_images_logic(mock_ecr_client):
    """
    Test the image cleanup logic:
    - Should delete untagged images.
    - Should keep N newest tagged images.
    - Should delete older tagged images.
    """
    repo_name = "test-repo"

    # Mock list_images (returns IDs)
    paginator = MagicMock()
    mock_ecr_client.get_paginator.return_value = paginator

    # 3 Tagged images, 1 Untagged
    mock_images = [
        {"imageDigest": "sha:untagged", "imageIds": "id1"},  # Untagged
        {"imageDigest": "sha:old", "imageTag": "v1"},
        {"imageDigest": "sha:mid", "imageTag": "v2"},
        {"imageDigest": "sha:new", "imageTag": "v3"},
    ]
    paginator.paginate.return_value = [{"imageIds": mock_images}]

    # Mock describe_images (returns details with timestamps for sorting)
    # Note: The code chunks calls to describe_images for tagged images only
    mock_ecr_client.describe_images.return_value = {
        "imageDetails": [
            {
                "imageDigest": "sha:old",
                "imageTags": ["v1"],
                "imagePushedAt": datetime.datetime(2023, 1, 1),
            },
            {
                "imageDigest": "sha:mid",
                "imageTags": ["v2"],
                "imagePushedAt": datetime.datetime(2023, 1, 2),
            },
            {
                "imageDigest": "sha:new",
                "imageTags": ["v3"],
                "imagePushedAt": datetime.datetime(2023, 1, 3),
            },
        ]
    }

    # Run command: Keep 1, delete untagged
    result = runner.invoke(
        awsbot_cli.commands.ecr.app,
        ["cleanup-images", repo_name, "--keep", "1", "--delete-untagged"],
    )

    assert result.exit_code == 0
    assert "Deleted 3 images" in result.stdout
    # Why 3? 1 untagged + 2 stale (old & mid) = 3 deleted.

    # Verify batch_delete_image calls
    # Call 1: Untagged images (logic separates them immediately)
    # Call 2: Stale tagged images (after sorting)
    # Depending on implementation, they might be batched together or separate.
    # Your code adds both to `to_delete` list and batches that list.

    assert mock_ecr_client.batch_delete_image.called

    # Extract all deleted IDs across all calls
    deleted_ids = []
    for call in mock_ecr_client.batch_delete_image.call_args_list:
        deleted_ids.extend(call[1]["imageIds"])  # 'imageIds' is a keyword arg

    # Verify Untagged was deleted
    assert any(d["imageDigest"] == "sha:untagged" for d in deleted_ids)

    # Verify Old and Mid were deleted
    assert any(d["imageDigest"] == "sha:old" for d in deleted_ids)
    assert any(d["imageDigest"] == "sha:mid" for d in deleted_ids)

    # Verify New was NOT deleted
    assert not any(d["imageDigest"] == "sha:new" for d in deleted_ids)


def test_cleanup_dry_run(mock_ecr_client):
    """Test dry run mode does not call delete."""
    paginator = MagicMock()
    mock_ecr_client.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {"imageIds": [{"imageDigest": "sha:1"}]}
    ]  # 1 untagged

    result = runner.invoke(
        awsbot_cli.commands.ecr.app, ["cleanup-images", "repo", "--dry-run"]
    )

    assert result.exit_code == 0
    assert "[DRY RUN]" in result.stdout
    mock_ecr_client.batch_delete_image.assert_not_called()
