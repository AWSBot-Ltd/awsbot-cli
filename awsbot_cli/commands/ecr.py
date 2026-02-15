import json
from typing import List

import boto3
import typer
from botocore.exceptions import ClientError

app = typer.Typer(help="Manage AWS ECR Repositories and Permissions")


def get_ecr_client():
    return boto3.client("ecr")


def generate_policy(push_arns: List[str], pull_arns: List[str]):
    """
    Generates a generic resource policy for ECR.
    """
    statements = []

    # 1. Statement for Pull (Read-Only)
    if pull_arns:
        statements.append(
            {
                "Sid": "AllowPull",
                "Effect": "Allow",
                "Principal": {"AWS": pull_arns},
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
            }
        )

    # 2. Statement for Push & Pull (Read-Write)
    if push_arns:
        statements.append(
            {
                "Sid": "AllowPushPull",
                "Effect": "Allow",
                "Principal": {"AWS": push_arns},
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:CompleteLayerUpload",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                ],
            }
        )

    if not statements:
        return None

    return json.dumps({"Version": "2012-10-17", "Statement": statements})


@app.command("create")
def create_repo(
    repo_name: str = typer.Argument(..., help="Name of the repository"),
    scan_on_push: bool = typer.Option(True, help="Enable image scanning on push"),
    allow_push: List[str] = typer.Option(
        [], "--allow-push", help="ARN of user/role allowed to PUSH (e.g., GitLab)"
    ),
    allow_pull: List[str] = typer.Option(
        [], "--allow-pull", help="ARN of user/role allowed to PULL (e.g., EKS Nodes)"
    ),
):
    """
    Create a repo and immediately grant permissions to EKS/GitLab/Users.
    """
    client = get_ecr_client()
    try:
        # 1. Create
        _ = client.create_repository(
            repositoryName=repo_name,
            imageScanningConfiguration={"scanOnPush": scan_on_push},
        )
        typer.secho(f"Created repository: {repo_name}", fg=typer.colors.GREEN)

        # 2. Apply Policy if ARNs provided
        policy_text = generate_policy(allow_push, allow_pull)
        if policy_text:
            client.set_repository_policy(
                repositoryName=repo_name, policyText=policy_text
            )
            typer.secho("Permissions applied successfully.", fg=typer.colors.GREEN)

    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@app.command("grant")
def grant_permission(
    repo_name: str = typer.Argument(..., help="Name of the repository"),
    principal_arn: str = typer.Argument(
        ..., help="The IAM ARN (Role/User) to grant access"
    ),
    access: str = typer.Option("pull", help="Level of access: 'pull' or 'push'"),
):
    """
    Add a specific permission to an existing repository.
    WARNING: This overwrites existing policies.
    """
    client = get_ecr_client()
    try:
        # Determine lists based on requested access
        push_list = [principal_arn] if access == "push" else []
        pull_list = [principal_arn] if access == "pull" else []

        policy_text = generate_policy(push_list, pull_list)

        client.set_repository_policy(repositoryName=repo_name, policyText=policy_text)
        typer.secho(
            f"Granted {access} access to {principal_arn}", fg=typer.colors.GREEN
        )
    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@app.command("delete")
def delete_repo(
    repo_name: str = typer.Argument(..., help="Name of the repository"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force delete even if it contains images"
    ),
):
    """
    Delete an ECR repository.
    """
    client = get_ecr_client()
    try:
        client.delete_repository(repositoryName=repo_name, force=force)
        typer.secho(f"Deleted repository: {repo_name}", fg=typer.colors.GREEN)
    except ClientError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@app.command("list")
def list_repos():
    """
    List all ECR repositories.
    """
    client = get_ecr_client()
    paginator = client.get_paginator("describe_repositories")
    for page in paginator.paginate():
        for repo in page["repositories"]:
            typer.echo(f"- {repo['repositoryName']} ({repo['repositoryUri']})")


@app.command("cleanup-images")
def cleanup_images(
    repo_name: str = typer.Argument(..., help="Name of the repository"),
    keep: int = typer.Option(10, help="Number of tagged images to keep"),
    delete_untagged: bool = typer.Option(True, help="Delete all untagged images"),
    dry_run: bool = typer.Option(False, help="Preview deletions without executing"),
):
    """
    Removes untagged images and keeps only the last N tagged images.
    """
    client = get_ecr_client()
    try:
        # Fetch images
        paginator = client.get_paginator("list_images")
        all_images = []
        for page in paginator.paginate(repositoryName=repo_name):
            all_images.extend(page.get("imageIds", []))

        if not all_images:
            typer.echo(f"No images found in {repo_name}.")
            return

        # Separate tagged and untagged
        untagged = [img for img in all_images if "imageTag" not in img]
        tagged = [img for img in all_images if "imageTag" in img]

        to_delete = []

        # 1. Untagged
        if delete_untagged and untagged:
            to_delete.extend(untagged)

        # 2. Stale Tagged
        if len(tagged) > keep:
            # Get details to sort by date
            details = []
            # Chunking for API limits
            chunk_size = 100
            for i in range(0, len(tagged), chunk_size):
                batch = tagged[i : i + chunk_size]
                resp = client.describe_images(repositoryName=repo_name, imageIds=batch)
                details.extend(resp["imageDetails"])

            # Sort newest first
            details.sort(key=lambda x: x["imagePushedAt"], reverse=True)

            # Identify stale
            stale = details[keep:]
            for img in stale:
                to_delete.append(
                    {"imageDigest": img["imageDigest"], "imageTag": img["imageTags"][0]}
                )

        if not to_delete:
            typer.secho("No images need deleting.", fg=typer.colors.GREEN)
            return

        # Execute
        if dry_run:
            typer.secho(
                f"[DRY RUN] Would delete {len(to_delete)} images.",
                fg=typer.colors.YELLOW,
            )
        else:
            # Delete in batches
            for i in range(0, len(to_delete), 100):
                batch = to_delete[i : i + 100]
                client.batch_delete_image(repositoryName=repo_name, imageIds=batch)
            typer.secho(f"Deleted {len(to_delete)} images.", fg=typer.colors.GREEN)

    except ClientError as e:
        typer.secho(f"AWS Error: {e}", fg=typer.colors.RED)
