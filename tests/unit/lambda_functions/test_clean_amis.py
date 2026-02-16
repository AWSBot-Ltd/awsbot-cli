import os
import boto3
import pytest
from moto import mock_aws
from unittest.mock import patch

# Import the handler using the path provided
from awsbot_cli.lambda_functions.cleanup_amis import handler


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def ec2_client(aws_credentials):
    with mock_aws():
        yield boto3.client("ec2", region_name="us-east-1")


def setup_test_resources(ec2):
    """Helper to create dummy AMIs and instances."""
    # Create an AMI
    image = ec2.register_image(
        Name="test-ami-to-delete",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"SnapshotId": "snap-12345", "VolumeSize": 8},
            }
        ],
    )
    ami_id = image["ImageId"]

    # Tag the AMI
    ec2.create_tags(
        Resources=[ami_id],
        Tags=[
            {"Key": "Name", "Value": "target-cleanup"},
            {"Key": "Environment", "Value": "dev"},
        ],
    )

    return ami_id


@patch("awsbot_cli.lambda_functions.cleanup_amis.get_logger")
def test_handler_dry_run(mock_get_logger, ec2_client):
    """Test that dry_run mode doesn't actually delete anything."""
    ami_id = setup_test_resources(ec2_client)

    event = {"target_tag": "target-cleanup", "environment": "dev", "dry_run": True}

    response = handler(event, None)

    assert response["statusCode"] == 200
    assert "DRY RUN complete" in response["body"]
    assert response["details"]["cleanup"][0]["Status"] == "Would Deregister"

    # Verify AMI still exists
    images = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
    assert len(images) == 1


@patch("awsbot_cli.lambda_functions.cleanup_amis.get_logger")
def test_handler_live_delete(mock_get_logger, ec2_client):
    """Test that live mode actually deregisters the AMI."""
    setup_test_resources(ec2_client)

    event = {"target_tag": "target-cleanup", "environment": "dev", "dry_run": False}

    response = handler(event, None)

    assert response["statusCode"] == 200
    assert "LIVE DELETE complete" in response["body"]

    # Verify AMI is gone
    images = ec2_client.describe_images(Owners=["self"])["Images"]
    assert len(images) == 0


@patch("awsbot_cli.lambda_functions.cleanup_amis.get_logger")
def test_handler_skips_in_use_ami(mock_get_logger, ec2_client):
    """Test that an AMI used by an active instance is skipped."""
    ami_id = setup_test_resources(ec2_client)

    # Run an instance using that AMI
    ec2_client.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)

    event = {"target_tag": "target-cleanup", "environment": "dev", "dry_run": False}

    response = handler(event, None)

    # Check that cleanup is empty and in_use has data
    assert len(response["details"]["cleanup"]) == 0
    assert len(response["details"]["in_use"]) == 1
    assert response["details"]["in_use"][0]["AMI ID"] == ami_id

    # Verify AMI still exists in EC2
    images = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
    assert len(images) == 1
