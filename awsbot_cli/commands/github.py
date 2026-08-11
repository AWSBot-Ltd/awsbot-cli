import os
import re
import requests
import typer
from git import Repo, exc
from rich.console import Console

from awsbot_cli.workflow.ai_utils import get_gemini_issue_content


from awsbot_cli.workflow.constants import LOGO  # Import the logo for the footer

app = typer.Typer(help="GitHub Management (Issues, PRs, Repos)")
console = Console()


def get_git_remote_info():
    """
    Uses GitPython to extract owner and repo name from the local 'origin' remote.
    """
    try:
        # Search for a valid repo starting from the current directory
        repo = Repo(os.getcwd(), search_parent_directories=True)

        # Get the URL of the 'origin' remote
        url = repo.remotes.origin.url

        # Regex to handle HTTPS and SSH formats
        match = re.search(r"github\.com[:/](.+)/(.+?)(?:\.git)?$", url)
        if match:
            return match.group(1), match.group(2)
    except (exc.InvalidGitRepositoryError, AttributeError):
        # Not a git repo or no 'origin' remote found
        pass

    return None, None


def get_headers():
    """Retrieve token from environment."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        console.print("[bold red]Error:[/bold red] GITHUB_TOKEN not set.")
        raise typer.Exit(code=1)
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def get_api_base(org: str = None, repo: str = None):
    """Construct API base URL."""
    if org and repo:
        return f"https://api.github.com/repos/{org}/{repo}"
    return "https://api.github.com"


def get_git_context():
    """Returns (owner, repo_name, git_user_name)"""
    try:
        repo = Repo(os.getcwd(), search_parent_directories=True)
        url = repo.remotes.origin.url
        git_user = repo.config_reader().get_value("user", "name", default=None)

        match = re.search(r"github\.com[:/](.+)/(.+?)(?:\.git)?$", url)
        if match:
            return match.group(1), match.group(2), git_user
    except Exception:
        pass
    return None, None, None


def ensure_github_label(org, repo, name, color):
    """
    Checks if a label exists on GitHub. If not, creates it with the AI color.
    """
    headers = get_headers()
    # Strip # if present for the API check, though POST takes it
    clean_color = color.lstrip("#")
    url = f"https://api.github.com/repos/{org}/{repo}/labels/{name}"

    check_resp = requests.get(url, headers=headers)
    if check_resp.status_code == 404:
        console.print(f"🎨 Creating label [bold]{name}[/bold] with color {color}...")
        create_url = f"https://api.github.com/repos/{org}/{repo}/labels"
        requests.post(
            create_url, headers=headers, json={"name": name, "color": clean_color}
        )


@app.command("issue-create")
def create_issue(
    prompt: str = typer.Option(
        None, "--prompt", "-p", help="AI prompt to generate issue content"
    ),
    title: str = typer.Option(None, "--title", "-t", help="Manual Issue Title"),
    body: str = typer.Option("", "--body", "-b", help="Manual Issue Description"),
    repo: str = typer.Option(None, help="Repo name (auto-detected)"),
    org: str = typer.Option(None, help="Org name (auto-detected)"),
):
    """Create a new issue with AI content, labels, and footer."""
    det_org, det_repo, det_user = get_git_context()
    target_org = org or det_org
    target_repo = repo or det_repo

    if not target_org or not target_repo:
        console.print(
            "[bold red]Error:[/bold red] Could not detect GitHub repo context."
        )
        raise typer.Exit(1)

    final_title = title
    final_body = body
    label_names = []

    # 1. AI Content Generation
    if prompt:
        ai_content = get_gemini_issue_content(prompt)
        if ai_content:
            final_title = ai_content.get("title", "New Issue")
            final_body = ai_content.get("body", "")

            # Sync AI-suggested labels
            for lbl in ai_content.get("labels", []):
                name, color = lbl.get("name"), lbl.get("color", "5843AD")
                ensure_github_label(target_org, target_repo, name, color)
                label_names.append(name)
        else:
            console.print("[yellow]AI generation failed. Using manual input.[/yellow]")

    if not final_title:
        console.print("[bold red]Error:[/bold red] Title is required.")
        raise typer.Exit(1)

    # 2. Add the Standardized Footer (Replicated from gitlab_utils.py)
    footer_link = "[AWSBOT CLI](https://github.com/awsbot-ltd/awsbot-cli)"
    footer = (
        f"\n\n---\n"
        f"```text\n{LOGO}\n```\n"
        f"Automatically generated with the {footer_link}"
    )
    final_body += footer

    # 3. Submit Issue
    url = f"https://api.github.com/repos/{target_org}/{target_repo}/issues"
    payload = {
        "title": final_title,
        "body": final_body,
        "assignees": [det_user] if det_user else [],
        "labels": label_names,
    }

    response = requests.post(url, json=payload, headers=get_headers())

    if response.status_code == 201:
        issue_url = response.json()["html_url"]
        console.print(
            f"[green]Success![/green] Issue created by [bold]{det_user}[/bold]: {issue_url}"
        )
    else:
        console.print(f"[red]Failed:[/red] {response.status_code} - {response.text}")


@app.command("pr-update")
def update_pr(
    pr_number: int = typer.Argument(..., help="Pull Request Number"),
    repo: str = typer.Option(..., help="Repository name"),
    org: str = typer.Option("awsbot-ltd", help="Organization/Owner name"),
    state: str = typer.Option(None, help="New state: 'open' or 'closed'"),
    comment: str = typer.Option(None, help="Add a comment to the PR"),
):
    """Update a Pull Request (Close/Reopen) or Comment."""
    headers = get_headers()
    base_url = f"{get_api_base(org, repo)}/pulls/{pr_number}"

    # 1. Update State (Close/Open)
    if state:
        resp = requests.patch(base_url, json={"state": state}, headers=headers)
        if resp.status_code == 200:
            console.print(f"[green]PR #{pr_number} state updated to '{state}'.[/green]")
        else:
            console.print(f"[red]Failed to update state:[/red] {resp.text}")

    # 2. Post Comment
    if comment:
        comment_url = f"{get_api_base(org, repo)}/issues/{pr_number}/comments"
        resp = requests.post(comment_url, json={"body": comment}, headers=headers)
        if resp.status_code == 201:
            console.print(f"[green]Comment added to PR #{pr_number}.[/green]")
        else:
            console.print(f"[red]Failed to comment:[/red] {resp.text}")


@app.command("audit-repos")
def audit_repos(
    org: str = typer.Option("awsbot-ltd", help="Target Organization"),
    fix: bool = typer.Option(
        False, "--fix", help="Actually apply changes (Make private, delete forks)"
    ),
):
    """
    Audit Organization: Finds public repos (warns/fixes) and forks (warns/deletes).
    """
    headers = get_headers()
    page = 1

    console.print(
        f"Scanning [bold]{org}[/bold] (Mode: {'FIX' if fix else 'DRY RUN'})..."
    )

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?type=public&per_page=100&page={page}"
        response = requests.get(url, headers=headers)
        repos = response.json()

        if not repos or not isinstance(repos, list):
            break

        for repo in repos:
            name = repo["name"]
            is_fork = repo.get("fork", False)

            # Case 1: Cleanup Forks
            if is_fork:
                if fix:
                    console.print(f"🗑️  Deleting Fork: {name}...")
                    requests.delete(
                        f"https://api.github.com/repos/{org}/{name}", headers=headers
                    )
                else:
                    console.print(f"[yellow]Found Fork (Dry Run):[/yellow] {name}")

            # Case 2: Enforce Private
            else:
                if fix:
                    console.print(f"🔒 Making Private: {name}...")
                    requests.patch(
                        f"https://api.github.com/repos/{org}/{name}",
                        json={"private": True},
                        headers=headers,
                    )
                else:
                    console.print(
                        f"[yellow]Found Public Repo (Dry Run):[/yellow] {name}"
                    )

        page += 1


@app.command("transfer-all")
def transfer_all(
    target_org: str = typer.Argument(..., help="Organization to move repos INTO"),
    source_user: str = typer.Option(
        None, help="User to move FROM (defaults to authenticated user)"
    ),
):
    """
    Bulk transfer ALL repositories from a user to an organization.
    """
    headers = get_headers()

    # 1. Get Repos
    url = "https://api.github.com/user/repos?type=owner&per_page=100"
    repos = requests.get(url, headers=headers).json()

    if not repos:
        console.print("No repositories found to transfer.")
        return

    console.print(f"Found {len(repos)} repositories. Transferring to {target_org}...")

    # 2. Transfer Loop
    for repo in repos:
        name = repo["name"]
        owner = repo["owner"]["login"]

        # Skip if we specified a source user and this doesn't match
        if source_user and owner != source_user:
            continue

        console.print(f"Transferring {name}...")

        url = f"https://api.github.com/repos/{owner}/{name}/transfer"
        resp = requests.post(url, headers=headers, json={"new_owner": target_org})

        if resp.status_code == 202:
            console.print(f"✅ {name} transfer started.")
        else:
            console.print(f"❌ Failed {name}: {resp.text}")
