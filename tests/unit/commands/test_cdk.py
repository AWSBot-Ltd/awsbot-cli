# import subprocess
# import pytest
# from typer.testing import CliRunner
# from unittest.mock import patch, MagicMock
#
# # Import from your module path
# from awsbot_cli.commands.cdk import app, build_context, get_git_sha
#
# runner = CliRunner()
#
#
# # --- Utility Tests ---
#
# def test_get_git_sha_success():
#     with patch("subprocess.check_output") as mock_git:
#         mock_git.return_value = b"abc12345\n"
#         assert get_git_sha() == "abc12345"
#
#
# def test_get_git_sha_failure():
#     with patch("subprocess.check_output") as mock_git:
#         mock_git.side_effect = subprocess.CalledProcessError(1, "git")
#         assert get_git_sha() == "unknown"
#
#
# # --- Command Tests ---
#
# @pytest.mark.parametrize("component, expected_stacks", [
#     ("shared", ["PlatformSharedStack-dev"]),
#     ("ami", ["PlatformSharedStack-dev", "AmiBuilderStack-dev"]),
#     ("all", ["--all"]),
# ])
# @patch("awsbot_cli.commands.cdk.subprocess.run")
# @patch("awsbot_cli.commands.cdk.get_git_sha")
# def test_deploy_stack_mapping(mock_git, mock_run, component, expected_stacks):
#     mock_git.return_value = "sha123"
#
#     # Ensure component is passed as a string value
#     result = runner.invoke(app, ["deploy", component, "--env", "dev"])
#
#     # Check if Typer actually succeeded
#     assert result.exit_code == 0, f"Command failed with: {result.stdout}"
#
#     # Verify subprocess was called
#     assert mock_run.called
#     args, _ = mock_run.call_args
#     cmd_list = args[0]
#
#     for stack in expected_stacks:
#         assert stack in cmd_list
#
#
# @patch("awsbot_cli.commands.cdk.subprocess.run")
# @patch("awsbot_cli.commands.cdk.get_git_sha")
# def test_deploy_with_options(mock_git, mock_run):
#     mock_git.return_value = "sha123"
#
#     # Pass boolean flags as they would appear in CLI
#     result = runner.invoke(app, ["deploy", "compute", "--create-ec2", "--migrate-db"])
#
#     assert result.exit_code == 0
#
#     # Securely unpack call_args
#     assert mock_run.call_args is not None, "subprocess.run was never called"
#     args, _ = mock_run.call_args
#     cmd_list = args[0]
#
#     assert "create_ec2=true" in cmd_list
#     assert "migrate_db=true" in cmd_list
#
#
# @patch("awsbot_cli.commands.cdk.subprocess.run")
# @patch("awsbot_cli.commands.cdk.get_git_sha")
# def test_deploy_subprocess_failure(mock_git, mock_run):
#     mock_git.return_value = "sha123"
#     # Force the mock to raise the error
#     mock_run.side_effect = subprocess.CalledProcessError(1, "cdk deploy")
#
#     # IMPORTANT: catch_exceptions=False allows the error to bubble up to pytest
#     with pytest.raises(subprocess.CalledProcessError):
#         runner.invoke(app, ["deploy", "shared"], catch_exceptions=False)
