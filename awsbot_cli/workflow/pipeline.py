import re
from pathlib import Path

from awsbot_cli.workflow.ai_utils import (
    get_gemini_labels,
    get_gemini_review,
    get_gemini_summary,
)
from awsbot_cli.workflow.gitlab_utils import (
    post_gemini_review,
    run_command,
    update_gitlab_mr,
)
from awsbot_cli.workflow.jira_utils import update_jira_issue


def find_template():
    # 1. Get the directory where pipeline.py lives
    current_dir = Path(__file__).parent

    # 2. Define the path relative to this file
    package_template = current_dir / "merge_request_templates" / "default.md"

    # 3. Define a fallback for local project-level templates
    local_template = Path("./merge_request_templates/default.md")

    gitlab_template = Path(".gitlab/merge_request_templates/default.md")

    paths = [package_template, local_template, gitlab_template]

    for p in paths:
        if p.exists():
            return p
    return None


def get_jira_id(branch):
    match = re.search(r"STS-\d{4}", branch)
    if match:
        print(f"🆔 Found Jira ID: {match.group(0)}")
        return match.group(0)
    print(f"⚠️ No Jira ID found in branch: {branch}")
    return None


def run_ai_pipeline(update_mr: bool, update_jira: bool, review: bool = False):
    """
    Main logic for the AI code review workflow.
    """
    branch = run_command("git rev-parse --abbrev-ref HEAD")
    if not branch:
        print("❌ Error: Not in a git repository.")
        return

    jira_id = get_jira_id(branch)
    template_path = find_template()

    if not template_path:
        print("❌ Error: Template not found.")
        return

    with open(template_path, "r") as f:
        template_content = f.read()

    print(f"🚀 Detected Branch: {branch}")
    diff_content = run_command(f"glab mr diff {branch}")

    if not diff_content:
        print("❌ Error: Could not fetch diff.")
        return

    if review:
        review_text = get_gemini_review(diff_content)
        if review_text:
            post_gemini_review(branch, review_text)

    prompt = f"Using the following template, summarize the code changes.\n\nTEMPLATE:\n{template_content}\n\nDIFF:\n{diff_content}"
    summary = get_gemini_summary(prompt)

    if not summary:
        return

    # 2. Generate Tags (New Step)
    tags = get_gemini_labels(diff_content)

    # 3. Update Platforms
    if update_mr:
        # Pass the tags list to your existing update function
        update_gitlab_mr(branch, summary, labels=tags)

    if update_jira and jira_id:
        update_jira_issue(jira_id, summary)

    print("\n✨ Done.")
