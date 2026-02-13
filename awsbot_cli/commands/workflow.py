import typer

from awsbot_cli.workflow.pipeline import run_ai_pipeline

app = typer.Typer(help="AI & DevOps Workflows")


@app.command("run")
def run(
    mr: bool = typer.Option(False, help="Update GitLab MR only"),
    jira: bool = typer.Option(False, help="Update Jira Issue only"),
    review: bool = typer.Option(
        False, "--review", help="Post an AI Code Review comment"
    ),
    all_systems: bool = typer.Option(False, "--all", help="Update both (Default)"),
):
    """Run the standard AI automation workflow."""
    if not (mr or jira or all_systems):
        all_systems = True

    # Pass pure booleans to the pipeline, not an 'args' object!
    run_ai_pipeline(
        update_mr=(mr or all_systems), update_jira=(jira or all_systems), review=review
    )
