from datetime import datetime
from unittest.mock import MagicMock

import boto3
import os
import pytest
from moto import mock_aws

from awsbot_cli.utils.scheduler import (
    apply_action,
    determine_action,
    discover_resources,
    list_all,
    resolve_days,
    tag_schedule,
)

SCHEDULE = {"start_time": "07:00", "stop_time": "20:00", "days": "weekdays"}


# --- determine_action (pure, no AWS) ---


def test_resolve_days_aliases():
    assert resolve_days("weekdays") == ["MON", "TUE", "WED", "THU", "FRI"]
    assert resolve_days("weekends") == ["SAT", "SUN"]
    assert resolve_days("mon, wed") == ["MON", "WED"]


def test_determine_action_in_window_weekday():
    # 2026-08-24 is a Monday
    at = datetime(2026, 8, 24, 8, 0)
    assert determine_action(SCHEDULE, at) == "start"


def test_determine_action_out_of_window_weekday():
    at = datetime(2026, 8, 24, 21, 0)
    assert determine_action(SCHEDULE, at) == "stop"


def test_determine_action_weekend():
    # 2026-08-29 is a Saturday
    at = datetime(2026, 8, 29, 8, 0)
    assert determine_action(SCHEDULE, at) == "stop"


# --- moto-backed ---


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
def asg_client(aws_credentials):
    with mock_aws():
        yield boto3.client("autoscaling", region_name="us-east-1")


@pytest.fixture
def rds_client(aws_credentials):
    with mock_aws():
        yield boto3.client("rds", region_name="us-east-1")


def test_discover_resources_only_tagged_instances(ec2_client, asg_client):
    tagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    tagged_id = tagged["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[tagged_id], Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )

    untagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    untagged_id = untagged["Instances"][0]["InstanceId"]

    resources = discover_resources(ec2_client, asg_client, "office-hours")

    found_ids = {i["InstanceId"] for i in resources["instances"]}
    assert found_ids == {tagged_id}
    assert untagged_id not in found_ids


def test_apply_action_dry_run_does_not_stop(ec2_client, asg_client):
    resp = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]
    resources = {
        "instances": [{"InstanceId": instance_id, "State": "running", "Tags": {}}],
        "asgs": [],
    }

    rows = apply_action(ec2_client, asg_client, "stop", resources, dry_run=True)

    assert rows[0]["status"] == "Would stop"
    state = ec2_client.describe_instances(InstanceIds=[instance_id])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "running"


def test_apply_action_stop_live(ec2_client, asg_client):
    resp = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]
    resources = {
        "instances": [{"InstanceId": instance_id, "State": "running", "Tags": {}}],
        "asgs": [],
    }

    rows = apply_action(ec2_client, asg_client, "stop", resources, dry_run=False)

    assert rows[0]["status"] == "Stopped"
    state = ec2_client.describe_instances(InstanceIds=[instance_id])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] in (
        "stopping",
        "stopped",
    )


def test_list_all_reports_tagged_and_untagged(ec2_client, asg_client):
    tagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    tagged_id = tagged["Instances"][0]["InstanceId"]
    ec2_client.create_tags(
        Resources=[tagged_id], Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )

    untagged = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    untagged_id = untagged["Instances"][0]["InstanceId"]

    resources = list_all(ec2_client, asg_client)

    by_id = {i["InstanceId"]: i["Schedule"] for i in resources["instances"]}
    assert by_id[tagged_id] == "office-hours"
    assert by_id[untagged_id] is None


def test_apply_action_skips_eb_asg_not_ready(ec2_client, asg_client):
    resources = {
        "instances": [],
        "asgs": [
            {
                "AutoScalingGroupName": "awseb-e-abc-stack",
                "InstanceIds": [],
                "Tags": {"elasticbeanstalk:environment-name": "my-app-prod"},
            }
        ],
    }
    eb_client = MagicMock()
    eb_client.describe_environments.return_value = {
        "Environments": [
            {
                "EnvironmentName": "my-app-prod",
                "ApplicationName": "my-app",
                "Status": "Updating",
            }
        ]
    }

    rows = apply_action(
        ec2_client, asg_client, "stop", resources, dry_run=False, eb_client=eb_client
    )

    assert "Skipped" in rows[0]["status"]
    assert "Updating" in rows[0]["status"]


def test_apply_action_proceeds_when_eb_ready(ec2_client, asg_client):
    resp = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]
    asg_client.create_launch_configuration(
        LaunchConfigurationName="lc-1", ImageId="ami-12345678", InstanceType="t3.micro"
    )
    asg_client.create_auto_scaling_group(
        AutoScalingGroupName="awseb-e-abc-stack",
        LaunchConfigurationName="lc-1",
        MinSize=0,
        MaxSize=1,
        AvailabilityZones=["us-east-1a"],
    )
    resources = {
        "instances": [],
        "asgs": [
            {
                "AutoScalingGroupName": "awseb-e-abc-stack",
                "InstanceIds": [instance_id],
                "Tags": {"elasticbeanstalk:environment-name": "my-app-prod"},
            }
        ],
    }
    eb_client = MagicMock()
    eb_client.describe_environments.return_value = {
        "Environments": [
            {
                "EnvironmentName": "my-app-prod",
                "ApplicationName": "my-app",
                "Status": "Ready",
            }
        ]
    }

    rows = apply_action(
        ec2_client, asg_client, "stop", resources, dry_run=False, eb_client=eb_client
    )

    assert rows[0]["status"] == "Stopped"


def _create_db_instance(rds_client, db_id: str):
    rds_client.create_db_instance(
        DBInstanceIdentifier=db_id,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="password123",
        AllocatedStorage=20,
    )


def test_discover_resources_only_tagged_db_instances(
    ec2_client, asg_client, rds_client
):
    _create_db_instance(rds_client, "tagged-db")
    _create_db_instance(rds_client, "untagged-db")
    arn = rds_client.describe_db_instances(DBInstanceIdentifier="tagged-db")[
        "DBInstances"
    ][0]["DBInstanceArn"]
    rds_client.add_tags_to_resource(
        ResourceName=arn, Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )

    resources = discover_resources(
        ec2_client, asg_client, "office-hours", rds_client=rds_client
    )

    found_ids = {d["DBInstanceIdentifier"] for d in resources["db_instances"]}
    assert found_ids == {"tagged-db"}


def test_discover_resources_no_rds_client_returns_empty(ec2_client, asg_client):
    resources = discover_resources(ec2_client, asg_client, "office-hours")
    assert resources["db_instances"] == []


def test_apply_action_stops_db_instance(ec2_client, asg_client, rds_client):
    _create_db_instance(rds_client, "my-db")
    resources = {
        "instances": [],
        "asgs": [],
        "db_instances": [
            {"DBInstanceIdentifier": "my-db", "Status": "available", "Tags": {}}
        ],
    }

    rows = apply_action(
        ec2_client, asg_client, "stop", resources, dry_run=False, rds_client=rds_client
    )

    assert rows[0]["status"] == "Stopped"
    assert rows[0]["type"] == "db_instance"


def test_apply_action_dry_run_does_not_stop_db_instance(
    ec2_client, asg_client, rds_client
):
    _create_db_instance(rds_client, "my-db")
    resources = {
        "instances": [],
        "asgs": [],
        "db_instances": [
            {"DBInstanceIdentifier": "my-db", "Status": "available", "Tags": {}}
        ],
    }

    rows = apply_action(
        ec2_client, asg_client, "stop", resources, dry_run=True, rds_client=rds_client
    )

    assert rows[0]["status"] == "Would stop"
    status = rds_client.describe_db_instances(DBInstanceIdentifier="my-db")[
        "DBInstances"
    ][0]["DBInstanceStatus"]
    assert status == "available"


def test_list_all_reports_db_instances(ec2_client, asg_client, rds_client):
    _create_db_instance(rds_client, "tagged-db")
    arn = rds_client.describe_db_instances(DBInstanceIdentifier="tagged-db")[
        "DBInstances"
    ][0]["DBInstanceArn"]
    rds_client.add_tags_to_resource(
        ResourceName=arn, Tags=[{"Key": "Schedule", "Value": "office-hours"}]
    )
    _create_db_instance(rds_client, "untagged-db")

    resources = list_all(ec2_client, asg_client, rds_client=rds_client)

    by_id = {
        d["DBInstanceIdentifier"]: d["Schedule"] for d in resources["db_instances"]
    }
    assert by_id["tagged-db"] == "office-hours"
    assert by_id["untagged-db"] is None


def test_tag_schedule_db_instance(ec2_client, asg_client, rds_client):
    _create_db_instance(rds_client, "my-db")

    tag_schedule(
        ec2_client,
        asg_client,
        "db_instance",
        ["my-db"],
        "office-hours",
        rds_client=rds_client,
    )

    tags = rds_client.describe_db_instances(DBInstanceIdentifier="my-db")[
        "DBInstances"
    ][0]["TagList"]
    assert {"Key": "Schedule", "Value": "office-hours"} in tags


def test_tag_schedule_instance(ec2_client, asg_client):
    resp = ec2_client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]

    tag_schedule(ec2_client, asg_client, "instance", [instance_id], "office-hours")

    tags = ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0][
        "Instances"
    ][0]["Tags"]
    assert {"Key": "Schedule", "Value": "office-hours"} in tags
