import os

import requests


# --- CONFIGURATION ---
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
ORG_NAME = "awsbot-ltd"
SAFE_MODE = False
DRY_RUN = False

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def manage_org_repos():
    page = 1
    processed_count = 0

    print(f"--- {'DRY RUN ACTIVE' if DRY_RUN else 'LIVE MODE'} ---")
    print(f"Targeting Organization: {ORG_NAME}\n")

    while True:
        # Fetch only public repositories
        url = f"https://api.github.com/orgs/{ORG_NAME}/repos?type=public&per_page=100&page={page}"
        response = requests.get(url, headers=HEADERS)

        if response.status_code != 200:
            print(
                f"❌ Error: {response.status_code} - {response.json().get('message')}"
            )
            break

        repos = response.json()
        if not repos:
            break

        for repo in repos:
            name = repo["name"]
            if DRY_RUN:
                print(f"[WILL UPDATE] {name} -> Private")
            else:
                patch_url = f"https://api.github.com/repos/{ORG_NAME}/{name}"
                res = requests.patch(patch_url, headers=HEADERS, json={"private": True})
                if res.status_code == 200:
                    print(f"✅ Success: {name} is now Private")
                else:
                    print(f"❌ Failed: {name} - {res.json().get('message')}")

            processed_count += 1

        page += 1

    print(f"\nFinished. Total repositories processed: {processed_count}")


def clean_org_forks():
    page = 1
    forks_found = []

    print(f"🔍 Scanning {ORG_NAME} for public forks...")

    # 1. Discover all repositories that are forks
    while True:
        url = f"https://api.github.com/orgs/{ORG_NAME}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=HEADERS)

        if response.status_code != 200:
            print(f"❌ Error fetching repos: {response.json().get('message')}")
            break

        repos = response.json()
        if not repos:
            break

        for repo in repos:
            # The 'fork' key is a boolean returned by GitHub API
            if repo.get("fork") is True:
                forks_found.append(repo["name"])

        page += 1

    if not forks_found:
        print("✅ No public forks found.")
        return

    print(f"\nFound {len(forks_found)} forks:")
    for name in forks_found:
        print(f" - {name}")

    # 2. Safety Check
    if SAFE_MODE:
        print("\n--- SAFE MODE ACTIVE ---")
        print("Change SAFE_MODE = False in the script to delete these.")
        return

    confirm = input(
        f"\n⚠️  DANGER: You are about to PERMANENTLY DELETE {len(forks_found)} repos. Type '{ORG_NAME}' to confirm: "
    )
    if confirm != ORG_NAME:
        print("Confirmation failed. Aborting.")
        return

    # 3. Execution
    for repo_name in forks_found:
        delete_url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}"
        res = requests.delete(delete_url, headers=HEADERS)

        if res.status_code == 204:
            print(f"🗑️  Deleted: {repo_name}")
        else:
            print(f"❌ Failed to delete {repo_name}: {res.status_code}")


def get_user_repos():
    """
    Fetches all repositories for the authenticated user.
    Returns a list of repository details.
    """
    url = "https://api.github.com/user/repos"
    repos = []
    page = 1

    while True:
        response = requests.get(
            url, headers=HEADERS, params={"page": page, "per_page": 100}
        )
        response.raise_for_status()  # Handle errors
        data = response.json()

        if not data:
            break

        repos.extend(data)
        page += 1

    return repos


def transfer_repo_to_org(repo_name, org_name):
    """
    Transfers a repository to a specified organization.
    Args:
    - repo_name: The name of the repository to transfer
    - org_name: The organization name to transfer to
    """
    url = f"https://api.github.com/repos/awsbot-labs/{repo_name}/transfer"
    payload = {"new_owner": org_name}

    response = requests.post(url, json=payload, headers=HEADERS)
    response.raise_for_status()  # Handle errors
    return response.json()


def transfer_all_repos_to_org(org_name):
    """
    Transfers all repositories owned by the authenticated user
    to the specified organization.
    Args:
    - org_name: The name of the target organization
    """
    repos = get_user_repos()

    for repo in repos:
        repo_name = repo["name"]
        print(f"Transferring {repo_name} to {org_name}...")
        transfer_repo_to_org(repo_name, org_name)
        print(f"Transferred {repo_name} successfully!")


if __name__ == "__main__":
    # Ensure the GitHub token and organization name are set correctly
    if not GITHUB_TOKEN or not ORG_NAME:
        print("Please set your GITHUB_TOKEN and ORG_NAME correctly.")
    else:
        transfer_all_repos_to_org(ORG_NAME)
