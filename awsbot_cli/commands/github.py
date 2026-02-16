import os
import requests
import typer
from rich.console import Console

app = typer.Typer(help="GitHub Management (Issues, PRs, Repos)")
console = Console()


# --- Helpers ---


def get_headers():
    """Retrieve token from environment (set by main.py profile loader)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        console.print(
            "[bold red]Error:[/bold red] GITHUB_TOKEN not set in this profile."
        )
        console.print("Run 'awsbot-cli auth configure --github-token <TOKEN>' first.")
        raise typer.Exit(code=1)
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def get_api_base(org: str = None, repo: str = None):
    """Construct API base URL."""
    if org and repo:
        return f"https://api.github.com/repos/{org}/{repo}"
    if org:
        return f"https://api.github.com/orgs/{org}"
    return "https://api.github.com"


# --- NEW: Issues & PRs ---


@app.command("issue-create")
def create_issue(
    repo: str = typer.Option(..., help="Repository name"),
    org: str = typer.Option(..., help="Organization/Owner name"),
    title: str = typer.Option(..., help="Issue Title"),
    body: str = typer.Option("", help="Issue Description"),
    assignee: str = typer.Option(None, help="GitHub username to assign"),
):
    """Create a new issue in a repository."""
    url = f"{get_api_base(org, repo)}/issues"
    payload = {"title": title, "body": body}
    if assignee:
        payload["assignees"] = [assignee]

    response = requests.post(url, json=payload, headers=get_headers())

    if response.status_code == 201:
        data = response.json()
        console.print(f"[green]Success![/green] Issue created: {data['html_url']}")
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


# --- PORTED: Repo Management ---


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
