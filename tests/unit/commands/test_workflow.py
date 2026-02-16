# import pytest
# from unittest.mock import patch
# from typer.testing import CliRunner
# # Ensure this matches the exact file location
# from awsbot_cli.commands.workflow import app
#
# runner = CliRunner()
#
#
# @pytest.fixture
# def mock_pipeline():
#     # Patching where it's USED in the commands/workflow.py file
#     with patch("awsbot_cli.commands.workflow.run_ai_pipeline") as mock:
#         yield mock
#
#
# @pytest.mark.unit
# def test_run_pipeline_default(mock_pipeline):
#     """Test default: no flags should trigger both MR and Jira."""
#     result = runner.invoke(app, ["run"])
#
#     # If this fails, the print will show the Typer usage error message
#     if result.exit_code != 0:
#         print(f"STDOUT: {result.stdout}")
#         print(f"STDERR: {result.stderr}")
#
#     assert result.exit_code == 0
#     mock_pipeline.assert_called_once_with(
#         update_mr=True,
#         update_jira=True,
#         review=False
#     )
#
#
# @pytest.mark.unit
# def test_run_pipeline_mr_only(mock_pipeline):
#     """Test only MR update."""
#     # Note: We use the option string exactly as defined in your code
#     result = runner.invoke(app, ["run", "--mr"])
#
#     assert result.exit_code == 0
#     mock_pipeline.assert_called_once_with(
#         update_mr=True,
#         update_jira=False,
#         review=False
#     )
#
#
# @pytest.mark.unit
# def test_run_pipeline_all_explicit(mock_pipeline):
#     """Test the --all flag."""
#     result = runner.invoke(app, ["run", "--all"])
#
#     assert result.exit_code == 0
#     mock_pipeline.assert_called_once_with(
#         update_mr=True,
#         update_jira=True,
#         review=False
#     )
#
#
# @pytest.mark.unit
# def test_run_pipeline_review_logic(mock_pipeline):
#     """Passing ONLY --review should still trigger MR/Jira based on your 'if not' logic."""
#     result = runner.invoke(app, ["run", "--review"])
#
#     assert result.exit_code == 0
#     mock_pipeline.assert_called_once_with(
#         update_mr=True,
#         update_jira=True,
#         review=True
#     )
