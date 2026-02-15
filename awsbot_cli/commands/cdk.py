import subprocess
from enum import Enum
from typing import List

import typer

app = typer.Typer(help="Manage AWS CDK deployments and infrastructure.")

# Define your default variables (from your Makefile)
DEFAULT_PYTHON_VERSION = "3.11"
DEFAULT_POETRY_VERSION = "1.3.2"
DEFAULT_UBUNTU_VERSION = "24.04"
DEFAULT_ARCH = "arm64"


class DeployComponent(str, Enum):
    """The available infrastructure components to deploy."""

    all = "all"
    shared = "shared"
    ami = "ami"
    compute = "compute"
    eks = "eks"
    health = "health"


def get_git_sha() -> str:
    """Capture the Git SHA."""
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"])
            .decode("utf-8")
            .strip()
        )
    except subprocess.CalledProcessError:
        return "unknown"


def build_context(
    environment: str,
    create_ec2: bool,
    migrate_db: bool,
) -> List[str]:
    """Generates the CDK_CONTEXT list."""
    git_sha = get_git_sha()
    name_prefix = (
        f"Steamaco-Ubuntu-{DEFAULT_UBUNTU_VERSION}-Python{DEFAULT_PYTHON_VERSION}"
    )

    return [
        "-c",
        f"create_ec2={'true' if create_ec2 else 'false'}",
        "-c",
        f"migrate_db={'true' if migrate_db else 'false'}",
        "-c",
        f"env_type={environment}",
        "-c",
        f"git_sha={git_sha}",
        "-c",
        f"python_version={DEFAULT_PYTHON_VERSION}",
        "-c",
        f"poetry_version={DEFAULT_POETRY_VERSION}",
        "-c",
        f"ubuntu_version={DEFAULT_UBUNTU_VERSION}",
        "-c",
        f"arch={DEFAULT_ARCH}",
        "-c",
        f"name_prefix={name_prefix}",
    ]


@app.command("deploy")
def deploy(
    component: DeployComponent = typer.Argument(
        ..., help="The infrastructure component to deploy"
    ),
    environment: str = typer.Option("dev", "--env", "-e", help="Target environment"),
    create_ec2: bool = typer.Option(False, help="Create EC2 instances"),
    migrate_db: bool = typer.Option(False, help="Migrate database"),
):
    """Deploy a specific infrastructure component or everything at once."""
    context = build_context(environment, create_ec2, migrate_db)

    # Map the chosen component to the actual CDK stack names
    stacks = []
    if component == DeployComponent.all:
        stacks = ["--all"]
    elif component == DeployComponent.shared:
        stacks = [f"PlatformSharedStack-{environment}"]  #
    elif component == DeployComponent.ami:
        stacks = [
            f"PlatformSharedStack-{environment}",
            f"AmiBuilderStack-{environment}",
        ]  #
    elif component == DeployComponent.compute:
        stacks = [f"PlatformComputeStack-{environment}"]  #
    elif component == DeployComponent.eks:
        stacks = [f"PlatformEksStack-{environment}"]  #
    elif component == DeployComponent.health:
        stacks = [f"PlatformHealthCheckStack-{environment}"]  #

    cmd = (
        ["poetry", "run", "cdk", "deploy"]
        + stacks
        + ["--require-approval", "never"]
        + context
    )

    typer.echo(f"Deploying {component.value} for environment: {environment}...")
    subprocess.run(cmd, check=True)
