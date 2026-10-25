import os

import boto3
import pytest
from moto import mock_aws
from unittest.mock import patch

from awsbot_cli.lambda_functions.scheduler import handler


@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def ec2_client(aws_credentials):
    with mock_aws():
        yield boto3.client("ec2", region_name="us-east-1")


@pytest.fixture
def rds_client(aws_credentials):
    with mock_aws():
        yield boto3.client("rds", region_name="us-east-1")


@patch("awsbot_cli.lambda_functions.scheduler.get_logger")
def test_handler_stops_only_tagged_instance(mock_get_logger, ec2_client):
    tagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    tagged_id = tagged["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[tagged_id], Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )

    untagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    untagged_id = untagged["Instances"][0]["InstanceId"]

    response = handler({"schedule": "office-hours", "action": "stop"}, None)

    assert response["statusCode"] == 200
    result_ids = {r["resource_id"] for r in response["details"]["results"]}
    assert result_ids == {tagged_id}

    states = {
        i["InstanceId"]: i["State"]["Name"]
        for r in ec2_client.describe_instances()["Reservations"]
        for i in r["Instances"]
    }
    assert states[tagged_id] in ("stopping", "stopped")
    assert states[untagged_id] == "running"


@patch("awsbot_cli.lambda_functions.scheduler.get_logger")
def test_handler_stops_only_tagged_db_instance(mock_get_logger, ec2_client, rds_client):
    rds_client.create_db_instance(
        DBInstanceIdentifier="tagged-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="password123",
        AllocatedStorage=20,
    )
    arn = rds_client.describe_db_instances(DBInstanceIdentifier="tagged-db")[
        "DBInstances"
    ][0]["DBInstanceArn"]
    rds_client.add_tags_to_resource(
        ResourceName=arn, Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )
    rds_client.create_db_instance(
        DBInstanceIdentifier="untagged-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="password123",
        AllocatedStorage=20,
    )

    response = handler({"schedule": "office-hours", "action": "stop"}, None)

    result_ids = {r["resource_id"] for r in response["details"]["results"]}
    assert "tagged-db" in result_ids
    assert "untagged-db" not in result_ids


def test_scrub_for_log_strips_crlf():
    from awsbot_cli.lambda_functions.scheduler import scrub_for_log

    # CR/LF (a forged "INFO admin logged in" line) is flattened to spaces
    assert scrub_for_log("office-hours\nINFO forged") == "office-hours INFO forged"
    assert scrub_for_log("stop\r\nhacked") == "stop  hacked"
    # ordinary values are unchanged
    assert scrub_for_log("office-hours") == "office-hours"
    assert scrub_for_log(123) == "123"
