from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import awsbot_cli.commands.scheduler

runner = CliRunner()


@pytest.fixture
def mock_boto_session():
    with patch("awsbot_cli.commands.scheduler.boto3.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        yield mock_session


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr("awsbot_cli.utils.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("awsbot_cli.utils.config.APP_DIR", tmp_path)


def test_set_and_list_schedule(tmp_config):
    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["set-schedule", "office-hours", "--start", "07:00", "--stop", "20:00"],
    )
    assert result.exit_code == 0
    assert "Saved schedule 'office-hours'" in result.stdout

    result = runner.invoke(awsbot_cli.commands.scheduler.app, ["list-schedules"])
    assert result.exit_code == 0
    assert "office-hours" in result.stdout


def test_run_dry_run_reports_no_resources(tmp_config, mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["run", "--schedule", "office-hours", "--action", "stop"],
    )
    assert result.exit_code == 0
    assert "No resources tagged Schedule=office-hours" in result.stdout


def test_run_dry_run_stops_tagged_instance(tmp_config, mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-123",
                            "State": {"Name": "running"},
                            "Tags": [{"Key": "Schedule", "Value": "office-hours"}],
                        }
                    ]
                }
            ]
        }
    ]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["run", "--schedule", "office-hours", "--action", "stop"],
    )
    assert result.exit_code == 0
    assert "i-123" in result.stdout
    assert "Would stop" in result.stdout
    ec2_client.stop_instances.assert_not_called()


def test_list_instances_shows_tagged_and_untagged(mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-tagged",
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": "Schedule", "Value": "office-hours"},
                                {"Key": "Name", "Value": "web-1"},
                            ],
                        },
                        {
                            "InstanceId": "i-untagged",
                            "State": {"Name": "running"},
                            "Tags": [],
                        },
                    ]
                }
            ]
        }
    ]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]

    result = runner.invoke(awsbot_cli.commands.scheduler.app, ["list-instances"])
    assert result.exit_code == 0
    assert "i-tagged" in result.stdout
    assert "i-untagged" in result.stdout
    assert "office-hours" in result.stdout
    assert "untagged" in result.stdout


def test_list_instances_untagged_only(mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-tagged",
                            "State": {"Name": "running"},
                            "Tags": [{"Key": "Schedule", "Value": "office-hours"}],
                        },
                        {
                            "InstanceId": "i-untagged",
                            "State": {"Name": "running"},
                            "Tags": [],
                        },
                    ]
                }
            ]
        }
    ]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app, ["list-instances", "--untagged-only"]
    )
    assert result.exit_code == 0
    assert "i-untagged" in result.stdout
    assert "i-tagged" not in result.stdout


def test_run_skips_eb_asg_mid_deploy(tmp_config, mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "awseb-e-abc123-stack-AWSEBAutoScalingGroup",
                    "Instances": [],
                    "Tags": [
                        {"Key": "Schedule", "Value": "office-hours"},
                        {
                            "Key": "elasticbeanstalk:environment-name",
                            "Value": "my-app-prod",
                        },
                    ],
                }
            ]
        }
    ]
    eb_client.describe_environments.return_value = {
        "Environments": [
            {
                "EnvironmentName": "my-app-prod",
                "ApplicationName": "my-app",
                "Status": "Updating",
            }
        ]
    }

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["run", "--schedule", "office-hours", "--action", "stop", "--apply"],
    )
    assert result.exit_code == 0
    assert "Skipped" in result.stdout
    assert "Updating" in result.stdout
    asg_client.suspend_processes.assert_not_called()


def test_list_instances_shows_eb_app_and_environment(mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()
    rds_client.get_paginator.return_value.paginate.return_value = [{"DBInstances": []}]

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "awseb-e-abc123-stack-AWSEBAutoScalingGroup",
                    "Tags": [
                        {
                            "Key": "elasticbeanstalk:environment-name",
                            "Value": "my-app-prod",
                        }
                    ],
                }
            ]
        }
    ]
    eb_client.describe_environments.return_value = {
        "Environments": [
            {
                "EnvironmentName": "my-app-prod",
                "ApplicationName": "my-app",
                "Status": "Ready",
            }
        ]
    }

    result = runner.invoke(awsbot_cli.commands.scheduler.app, ["list-instances"])
    assert result.exit_code == 0
    assert "my-app" in result.stdout
    assert "my-app-prod" in result.stdout


def test_tag_requires_target(mock_boto_session):
    result = runner.invoke(
        awsbot_cli.commands.scheduler.app, ["tag", "--schedule", "office-hours"]
    )
    assert result.exit_code == 1
    assert "provide at least one" in result.stdout


def test_tag_db_instance(mock_boto_session):
    rds_client = MagicMock()

    def client_side_effect(service_name):
        return {"ec2": MagicMock(), "autoscaling": MagicMock(), "rds": rds_client}[
            service_name
        ]

    mock_boto_session.client.side_effect = client_side_effect
    rds_client.describe_db_instances.return_value = {
        "DBInstances": [
            {"DBInstanceArn": "arn:aws:rds:us-east-1:123456789012:db:my-db"}
        ]
    }

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["tag", "--schedule", "office-hours", "--db-instance-id", "my-db"],
    )

    assert result.exit_code == 0
    assert "Tagged 1 DB instance(s)" in result.stdout
    rds_client.add_tags_to_resource.assert_called_once_with(
        ResourceName="arn:aws:rds:us-east-1:123456789012:db:my-db",
        Tags=[{"Key": "Schedule", "Value": "office-hours"}],
    )


def test_list_instances_shows_db_instances(mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]
    rds_client.get_paginator.return_value.paginate.return_value = [
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "my-db",
                    "DBInstanceStatus": "available",
                    "TagList": [{"Key": "Schedule", "Value": "office-hours"}],
                },
                {
                    "DBInstanceIdentifier": "untagged-db",
                    "DBInstanceStatus": "available",
                    "TagList": [],
                },
            ]
        }
    ]

    result = runner.invoke(awsbot_cli.commands.scheduler.app, ["list-instances"])
    assert result.exit_code == 0
    assert "my-db" in result.stdout
    assert "untagged-db" in result.stdout
    assert "office-hours" in result.stdout
    assert "untagged" in result.stdout


def test_run_stops_tagged_db_instance(tmp_config, mock_boto_session):
    ec2_client = MagicMock()
    asg_client = MagicMock()
    eb_client = MagicMock()
    rds_client = MagicMock()

    def client_side_effect(service_name):
        return {
            "ec2": ec2_client,
            "autoscaling": asg_client,
            "elasticbeanstalk": eb_client,
            "rds": rds_client,
        }[service_name]

    mock_boto_session.client.side_effect = client_side_effect

    ec2_client.get_paginator.return_value.paginate.return_value = [{"Reservations": []}]
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]
    rds_client.get_paginator.return_value.paginate.return_value = [
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "my-db",
                    "DBInstanceStatus": "available",
                    "TagList": [{"Key": "Schedule", "Value": "office-hours"}],
                }
            ]
        }
    ]

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["run", "--schedule", "office-hours", "--action", "stop", "--apply"],
    )

    assert result.exit_code == 0
    assert "my-db" in result.stdout
    rds_client.stop_db_instance.assert_called_once_with(DBInstanceIdentifier="my-db")


def test_cron_expr():
    expr = awsbot_cli.commands.scheduler._cron_expr("07:05", "weekdays")
    assert expr == "cron(5 7 ? * MON,TUE,WED,THU,FRI *)"


def test_sam_param_overrides():
    cfg = {"start_time": "07:00", "stop_time": "20:00", "days": "weekdays"}
    overrides = awsbot_cli.commands.scheduler._sam_param_overrides("office-hours", cfg)
    assert 'ScheduleName="office-hours"' in overrides
    assert 'StartCron="cron(0 7 ? * MON,TUE,WED,THU,FRI *)"' in overrides
    assert 'StopCron="cron(0 20 ? * MON,TUE,WED,THU,FRI *)"' in overrides


def test_stage_lambda_source(tmp_path, monkeypatch):
    staged = tmp_path / "src"
    monkeypatch.setattr(awsbot_cli.commands.scheduler, "SRC_DIR", staged)

    awsbot_cli.commands.scheduler._stage_lambda_source()

    assert (staged / "awsbot_cli" / "__init__.py").exists()
    assert (staged / "awsbot_cli" / "lambda_functions" / "scheduler.py").exists()
    assert (staged / "awsbot_cli" / "utils" / "scheduler.py").exists()
    assert (staged / "awsbot_cli" / "utils" / "logger.py").exists()

    # Re-running clears stale files rather than accumulating them.
    (staged / "awsbot_cli" / "stray.txt").write_text("stale")
    awsbot_cli.commands.scheduler._stage_lambda_source()
    assert not (staged / "awsbot_cli" / "stray.txt").exists()


def test_deploy_invokes_sam(tmp_config, monkeypatch):
    runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["set-schedule", "office-hours", "--start", "07:00", "--stop", "20:00"],
    )

    monkeypatch.setattr(
        awsbot_cli.commands.scheduler, "_stage_lambda_source", MagicMock()
    )
    mock_run = MagicMock()
    monkeypatch.setattr(awsbot_cli.commands.scheduler.subprocess, "run", mock_run)

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["deploy", "--schedule", "office-hours", "--region", "eu-west-1"],
    )

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["sam", "deploy"]
    assert "--stack-name" in cmd
    assert "awsbot-scheduler-office-hours" in cmd
    assert "--region" in cmd
    assert "eu-west-1" in cmd
    assert "awsbot-scheduler-office-hours-override" in result.stdout
    assert "Execute automation" in result.stdout


def test_deploy_missing_sam_cli(tmp_config, monkeypatch):
    runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["set-schedule", "office-hours", "--start", "07:00", "--stop", "20:00"],
    )
    monkeypatch.setattr(
        awsbot_cli.commands.scheduler, "_stage_lambda_source", MagicMock()
    )
    monkeypatch.setattr(
        awsbot_cli.commands.scheduler.subprocess,
        "run",
        MagicMock(side_effect=FileNotFoundError()),
    )

    result = runner.invoke(
        awsbot_cli.commands.scheduler.app, ["deploy", "--schedule", "office-hours"]
    )

    assert result.exit_code == 1
    assert "'sam' CLI not found" in result.stdout


def test_deploy_unknown_schedule(tmp_config, mock_boto_session):
    result = runner.invoke(
        awsbot_cli.commands.scheduler.app,
        ["deploy", "--schedule", "nope"],
    )
    assert result.exit_code == 1
    assert "unknown schedule" in result.stdout
