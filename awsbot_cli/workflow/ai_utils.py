import json
import os
import subprocess

from awsbot_cli.workflow.constants import GEMINI_MODEL


def get_gemini_summary(prompt_text):
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"

    try:
        print("💡 Sending diff via stdin to Gemini Flash...")

        # Use gemini-3-flash for maximum 2026 performance
        # We pass the prompt via -p and the diff via input=prompt_text
        process = subprocess.run(
            ["gemini", "-m", GEMINI_MODEL, "-p", "Summarize this diff:"],
            input=prompt_text,  # This sends the large text via stdin
            capture_output=True,
            text=True,
            env=env,
            timeout=120,  # Increased slightly for very large diffs
        )

        if process.returncode != 0:
            print(f"❌ Gemini CLI Error: {process.stderr}")
            return None

        return process.stdout.strip()

    except Exception as e:
        print(f"❌ Error calling Gemini CLI: {e}")
        return None


def get_gemini_labels(diff_text):
    """
    Analyzes the diff and returns a list of relevant tags.
    """
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"

    prompt = """
        Analyze the code changes and generate 1 to 3 relevant GitLab labels.

        For each label, choose a suitable Hex Color Code based on its meaning:
        - Red/Orange (#FF0000, #FFA500) for bugs, critical fixes, or security.
        - Blue/Purple (#428BCA, #6F42C1) for features, backend, or logic.
        - Green (#5CB85C) for CI/CD, tests, or chores.
        - Pink/Yellow for frontend, documentation, or minor tweaks.

        Return ONLY a raw JSON list of objects with "name" and "color" keys.
        Example:
        [
            {"name": "database-migration", "color": "#FF0000"},
            {"name": "backend", "color": "#6F42C1"}
        ]
        """

    try:
        print("🏷️  Asking Gemini to infer tags...")

        process = subprocess.run(
            ["gemini", "-m", GEMINI_MODEL, "-p", prompt],
            input=diff_text,  # Pass diff via stdin
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        if process.returncode != 0:
            print(f"❌ Gemini CLI Error: {process.stderr}")
            return []

        # Clean the output in case Gemini returns Markdown code blocks
        raw_output = process.stdout.strip()
        clean_json = raw_output.replace("```json", "").replace("```", "").strip()

        return json.loads(clean_json)

    except json.JSONDecodeError:
        print(f"⚠️ Could not parse JSON from Gemini: {raw_output}")
        return []
    except Exception as e:
        print(f"❌ Error calling Gemini CLI: {e}")
        return []


def get_gemini_review(diff_text):
    """
    Asks Gemini to review the code and return structured feedback.
    """
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"

    prompt = """
    You are a Senior Software Engineer. specific bugs, security risks, or logic errors in the following code diff.

    Format your response as a strict JSON list of objects. Each object must have:
    - "file": The file path (inferred from the diff headers).
    - "issue": A brief title of the issue.
    - "comment": The detailed explanation and suggestion.
    - "severity": "High", "Medium", or "Low".

    Do not comment on formatting or trivial style issues. Focus on logic and safety.

    Example response:
    [
        {
            "file": "src/main.py",
            "issue": "Potential SQL Injection",
            "comment": "Input string is not sanitized before query construction.",
            "severity": "High"
        }
    ]
    """

    try:
        print("🕵️  Asking Gemini to review code...")

        # Similar process call to your existing functions
        process = subprocess.run(
            ["gemini", "-m", GEMINI_MODEL, "-p", prompt],
            input=diff_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,  # Reviews might take longer
        )

        if process.returncode != 0:
            print(f"❌ Gemini CLI Error: {process.stderr}")
            return []

        # JSON Cleaning logic
        raw_output = process.stdout.strip()
        clean_json = raw_output.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)

    except Exception as e:
        print(f"❌ Error during review generation: {e}")
        return []
