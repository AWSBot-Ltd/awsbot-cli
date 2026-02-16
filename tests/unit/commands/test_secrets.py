import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from botocore.exceptions import ClientError

# Importing the app from your module path
from awsbot_cli.commands.secrets import app

runner = CliRunner()


@pytest.mark.unit
def test_create_secret_success():
    """Test successful secret creation."""
    # Mock data
    mock_name = "test-secret"
    mock_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret"

    with patch("awsbot_cli.commands.secrets.boto3.client") as mock_boto_client:
        # Setup the mock instance and its return value
        mock_sm = MagicMock()
        mock_boto_client.return_value = mock_sm
        mock_sm.create_secret.return_value = {"Name": mock_name, "ARN": mock_arn}

        # Execute the command
        result = runner.invoke(
            app, ["test-secret", "my-super-password", "--desc", "A test secret"]
        )

        # Assertions
        assert result.exit_code == 0
        assert "Success!" in result.stdout
        assert "arn:aws:secretsmanager" in result.stdout
        assert "test-secret" in result.stdout

        # Verify the client was called with correct parameters
        mock_sm.create_secret.assert_called_once_with(
            Name="test-secret",
            Description="A test secret",
            SecretString="my-super-password",
        )


@pytest.mark.unit
def test_create_secret_error():
    """Test secret creation failure (ClientError)."""
    with patch("awsbot_cli.commands.secrets.boto3.client") as mock_boto_client:
        # Setup the mock to raise a ClientError
        mock_sm = MagicMock()
        mock_boto_client.return_value = mock_sm

        error_response = {
            "Error": {
                "Code": "ResourceExistsException",
                "Message": "The secret already exists.",
            }
        }
        mock_sm.create_secret.side_effect = ClientError(error_response, "CreateSecret")

        # Execute the command
        result = runner.invoke(app, ["existing-secret", "value"])

        # Assertions
        assert result.exit_code == 1
        assert "Error:" in result.stdout
        assert "The secret already exists." in result.stdout
