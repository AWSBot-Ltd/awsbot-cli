import os
import subprocess

import gitlab

from awsbot_cli.workflow.constants import LOGO


def ensure_label_with_color(project, label_name, label_color):
    """
    Ensures the label exists. If not, creates it with the AI-suggested color.
    """
    try:
        # 1. Check if label exists
        project.labels.get(label_name)
    except gitlab.exceptions.GitlabGetError:
        # 2. If missing, create it with the dynamic color
        print(f"🎨 Creating new label '{label_name}' with color {label_color}...")
        try:
            project.labels.create({"name": label_name, "color": label_color})
        except gitlab.exceptions.GitlabCreateError as e:
            print(f"⚠️ Failed to create label: {e}")


def run_command(cmd, input_text=None):
    """
    Kept here to prevent ImportError in main.py.
    This uses subprocess to run general shell commands.
    """
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(input=input_text)
    return stdout.strip() if process.returncode == 0 else None


def get_project_path_from_git():
    """
    Extracts 'group/subgroup/project' from git remote URL.
    Handles git@gitlab.com:group/sub/project.git and https://gitlab.com/group/sub/project.git
    """
    try:
        url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"], text=True
        ).strip()

        # 1. Remove .git suffix if present
        if url.endswith(".git"):
            url = url[:-4]

        # 2. Handle SSH (git@gitlab.com:group/project)
        # Splits at the colon and takes the right side
        if ":" in url and not url.startswith("http"):
            return url.split(":")[-1]

        # 3. Handle HTTPS (https://gitlab.com/group/project)
        # Splits at gitlab.com/ and takes the right side
        if "gitlab.com/" in url:
            return url.split("gitlab.com/")[-1]

        return None
    except Exception:
        return None


def update_gitlab_mr(branch, summary, labels=None):
    token = os.getenv("GITLAB_TOKEN")
    project_path = get_project_path_from_git()

    if not token:
        print("❌ GITLAB_TOKEN environment variable not set.")
        return False

    if not project_path:
        print("❌ Could not determine Project Path from git remote.")
        return False

    try:
        # 1. Initialize API Client
        gl = gitlab.Gitlab("https://gitlab.com", private_token=token)

        # 2. Get Project Object
        project = gl.projects.get(project_path)

        # 3. Find the Open MR for this branch
        mrs = project.mergerequests.list(state="opened", source_branch=branch)

        if not mrs:
            print(f"⚠️ No open MR found for branch: {branch}")
            return False

        # 4. Update and Save
        mr = mrs[0]

        # --- Construct Description with Footer ---
        footer_link = "[AWSBOT CLI](https://gitlab.com/awsbot-ltd/awsbot-cli)"

        # We wrap the logo in ```text block to ensure it renders with fixed-width font
        footer = (
            f"\n\n---\n"
            f"```text\n{LOGO}\n```\n"
            f"Automatically generated with the {footer_link}"
        )

        mr.description = f"{summary}{footer}"

        # --- Update Labels with Colors ---
        if labels and isinstance(labels, list):
            current_labels = []

            for label_obj in labels:
                name = label_obj.get("name")
                color = label_obj.get("color", "#5843AD")  # Fallback color

                # Create/Verify the label exists on GitLab
                ensure_label_with_color(project, name, color)

                # Add to list for assignment
                current_labels.append(name)

            # Assign the list of names to the MR
            mr.labels = current_labels

        mr.save()

        print(f"✅ MR updated: {mr.web_url}")
        print(f"🏷️  Labels set to: {mr.labels}")
        return True

    except gitlab.exceptions.GitlabError as e:
        print(f"❌ GitLab API Error: {e}")
        return False


def post_gemini_review(branch, review_data):  # <--- Accept the data here
    token = os.getenv("GITLAB_TOKEN")
    project_path = get_project_path_from_git()

    if not token or not project_path:
        return False

    try:
        gl = gitlab.Gitlab("https://gitlab.com", private_token=token)
        project = gl.projects.get(project_path)
        mrs = project.mergerequests.list(state="opened", source_branch=branch)

        if not mrs:
            print(f"⚠️ No open MR found for branch: {branch}")
            return False

        mr = mrs[0]

        # --- REMOVED THE DUPLICATE AI CALL LOGIC ---

        if not review_data:
            print("⚠️ Review data is empty.")
            return False

        # 3. Format the Comment
        comment_body = "## 🤖 Gemini AI Code Review\n\n"
        comment_body += "| Severity | File | Issue | Suggestion |\n"
        comment_body += "| :--- | :--- | :--- | :--- |\n"

        # Ensure review_data is actually a list (error handling)
        if isinstance(review_data, list):
            for item in review_data:
                icon = "🔴" if item.get("severity") == "High" else "🟡"
                # Break the f-string into multiple parts
                comment_body += (
                    f"| {icon} {item.get('severity', 'Low')} "
                    f"| `{item.get('file', 'unknown')}` "
                    f"| **{item.get('issue', 'Issue')}** "
                    f"| {item.get('comment', '')} |\n"
                )
        else:
            comment_body += f"\n{review_data}"

        comment_body += "\n\n*Review generated automatically by Gemini Flash.*"

        # 4. Post as a Note
        mr.notes.create({"body": comment_body})
        print(f"✅ Posted AI review to MR: {mr.web_url}")

        return True

    except Exception as e:
        print(f"❌ Error posting review: {e}")
        return False
