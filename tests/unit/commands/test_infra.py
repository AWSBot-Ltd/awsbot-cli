import pytest
import requests  # Import real requests to get the real exception class
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

# Import the module to be tested
import awsbot_cli.commands.infra

runner = CliRunner()


# --- Fixtures ---


@pytest.fixture
def mock_requests():
    """Patches requests but keeps exceptions real."""
    with patch("awsbot_cli.commands.infra.requests") as mock_req:
        # CRITICAL FIX: Restore the real exception class so try/except blocks work
        mock_req.exceptions.RequestException = requests.exceptions.RequestException
        yield mock_req


@pytest.fixture
def mock_boto_session():
    with patch("awsbot_cli.commands.infra.boto3.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_ssm_connector():
    with patch("awsbot_cli.commands.infra.SSMConnector") as mock_connector:
        yield mock_connector


@pytest.fixture
def mock_cleanup_handler():
    with patch("awsbot_cli.commands.infra.cleanup_amis.handler") as mock_handler:
        yield mock_handler


# --- Tests ---


def test_find_target_instance_success(mock_boto_session):
    # Setup mocks
    asg_client = MagicMock()
    ec2_client = MagicMock()

    def client_side_effect(service_name):
        if service_name == "autoscaling":
            return asg_client
        if service_name == "ec2":
            return ec2_client
        return MagicMock()

    mock_boto_session.client.side_effect = client_side_effect

    paginator = MagicMock()
    asg_client.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "my-asg",
                    "Tags": [
                        {"Key": "Project", "Value": "alpha"},
                        {"Key": "Environment", "Value": "prod"},
                    ],
                    "Instances": [
                        {"InstanceId": "i-12345", "LifecycleState": "InService"}
                    ],
                }
            ]
        }
    ]

    ec2_client.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"PrivateIpAddress": "10.0.0.1"}]}]
    }

    inst_id, inst_ip = awsbot_cli.commands.infra.find_target_instance("alpha", "prod")
    assert inst_id == "i-12345"
    assert inst_ip == "10.0.0.1"


def test_find_target_instance_no_asg(mock_boto_session):
    asg_client = MagicMock()
    mock_boto_session.client.return_value = asg_client
    asg_client.get_paginator.return_value.paginate.return_value = [
        {"AutoScalingGroups": []}
    ]

    with pytest.raises(SystemExit) as exc:
        awsbot_cli.commands.infra.find_target_instance("beta", "dev")
    assert exc.value.code == 1


def test_find_target_instance_no_instances(mock_boto_session):
    asg_client = MagicMock()
    mock_boto_session.client.return_value = asg_client
    asg_client.get_paginator.return_value.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "Tags": [
                        {"Key": "Project", "Value": "alpha"},
                        {"Key": "Environment", "Value": "prod"},
                    ],
                    "Instances": [
                        {"InstanceId": "i-dead", "LifecycleState": "Terminating"}
                    ],
                }
            ]
        }
    ]

    with pytest.raises(SystemExit) as exc:
        awsbot_cli.commands.infra.find_target_instance("alpha", "prod")
    assert exc.value.code == 1


def test_connect_with_id(mock_ssm_connector):
    result = runner.invoke(awsbot_cli.commands.infra.app, ["connect", "i-manual"])
    assert result.exit_code == 0
    assert "Connecting to i-manual" in result.stdout
    mock_ssm_connector.return_value.start_interactive_session.assert_called_with(
        "i-manual"
    )


@patch("awsbot_cli.commands.infra.find_target_instance")
def test_connect_discovery(mock_find, mock_ssm_connector):
    mock_find.return_value = ("i-discovered", "1.2.3.4")
    result = runner.invoke(
        awsbot_cli.commands.infra.app, ["connect", "--project", "p", "--env", "e"]
    )

    assert result.exit_code == 0
    assert "Connecting to i-discovered" in result.stdout
    mock_find.assert_called_with("p", "e", None)
    mock_ssm_connector.return_value.start_interactive_session.assert_called_with(
        "i-discovered"
    )


def test_connect_missing_args():
    result = runner.invoke(awsbot_cli.commands.infra.app, ["connect"])
    assert result.exit_code == 1
    assert "must provide either an Instance ID OR" in result.stdout


def test_clean_amis(mock_cleanup_handler):
    mock_cleanup_handler.return_value = {
        "details": {
            "cleanup": [{"AMI ID": "ami-1", "Status": "available"}],
            "in_use": [{"AMI ID": "ami-2", "Instance ID": "i-1"}],
        }
    }
    result = runner.invoke(
        awsbot_cli.commands.infra.app, ["clean-amis", "--env", "dev"]
    )
    assert result.exit_code == 0
    assert "AMIS TO CLEAN UP" in result.stdout
    assert "ami-1" in result.stdout


def test_refresh_success(mock_boto_session):
    asg_client = MagicMock()
    mock_boto_session.client.return_value = asg_client

    paginator = MagicMock()
    asg_client.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "prod-asg",
                    "Tags": [
                        {"Key": "Project", "Value": "web"},
                        {"Key": "Environment", "Value": "prod"},
                    ],
                }
            ]
        }
    ]

    asg_client.start_instance_refresh.return_value = {"InstanceRefreshId": "ref-123"}
    asg_client.describe_instance_refreshes.return_value = {
        "InstanceRefreshes": [{"Status": "Successful", "PercentageComplete": 100}]
    }

    result = runner.invoke(
        awsbot_cli.commands.infra.app, ["refresh", "--project", "web", "--env", "prod"]
    )
    assert result.exit_code == 0
    assert "Refresh Completed Successfully" in result.stdout


def test_refresh_with_checkpoints(mock_boto_session):
    asg_client = MagicMock()
    mock_boto_session.client.return_value = asg_client

    paginator = MagicMock()
    asg_client.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "asg",
                    "Tags": [
                        {"Key": "Project", "Value": "p"},
                        {"Key": "Environment", "Value": "e"},
                    ],
                }
            ]
        }
    ]

    asg_client.start_instance_refresh.return_value = {"InstanceRefreshId": "ref-1"}
    asg_client.describe_instance_refreshes.return_value = {
        "InstanceRefreshes": [{"Status": "Successful"}]
    }

    runner.invoke(
        awsbot_cli.commands.infra.app,
        [
            "refresh",
            "--project",
            "p",
            "--env",
            "e",
            "--bake-time",
            "60",
            "--checkpoint-percentages",
            "10,50",
        ],
    )

    call_kwargs = asg_client.start_instance_refresh.call_args[1]
    prefs = call_kwargs["Preferences"]
    assert prefs["CheckpointDelay"] == 60
    assert prefs["CheckpointPercentages"] == [10, 50]


def test_check_health_success(mock_boto_session, mock_requests):
    cfn_client = MagicMock()
    mock_boto_session.client.return_value = cfn_client
    cfn_client.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"ExportName": "proj-env-url", "OutputValue": "http://api.internal"}
                ]
            }
        ]
    }

    mock_requests.get.return_value.status_code = 200

    result = runner.invoke(
        awsbot_cli.commands.infra.app,
        ["check-health", "--project", "proj", "--env", "env"],
    )
    assert result.exit_code == 0
    assert "Success! Service is Healthy" in result.stdout


def test_check_health_export_not_found(mock_boto_session):
    cfn_client = MagicMock()
    mock_boto_session.client.return_value = cfn_client
    cfn_client.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    result = runner.invoke(
        awsbot_cli.commands.infra.app,
        ["check-health", "--project", "proj", "--env", "env"],
    )
    assert result.exit_code == 1
    assert "Error: Export 'proj-env-url' not found" in result.stdout


def test_check_health_timeout_retry_logic(mock_boto_session, mock_requests):
    cfn_client = MagicMock()
    mock_boto_session.client.return_value = cfn_client
    cfn_client.describe_stacks.return_value = {
        "Stacks": [
            {"Outputs": [{"ExportName": "p-e-url", "OutputValue": "http://url"}]}
        ]
    }

    mock_requests.get.return_value.status_code = 500

    with patch("awsbot_cli.commands.infra.time.sleep"):
        result = runner.invoke(
            awsbot_cli.commands.infra.app,
            [
                "check-health",
                "--project",
                "p",
                "--env",
                "e",
                "--max-retries",
                "2",
                "--interval",
                "1",
            ],
        )

    assert result.exit_code == 1
    assert "Timeout: Service did not become healthy" in result.stdout
    assert mock_requests.get.call_count > 1


def test_check_health_redirect_logic(mock_boto_session, mock_requests):
    cfn_client = MagicMock()
    mock_boto_session.client.return_value = cfn_client
    cfn_client.describe_stacks.return_value = {
        "Stacks": [
            {"Outputs": [{"ExportName": "p-e-url", "OutputValue": "http://url"}]}
        ]
    }

    resp_redirect = MagicMock()
    resp_redirect.status_code = 308

    resp_ok = MagicMock()
    resp_ok.status_code = 200

    mock_requests.get.side_effect = [resp_redirect, resp_ok]

    with patch("awsbot_cli.commands.infra.time.sleep"):
        result = runner.invoke(
            awsbot_cli.commands.infra.app,
            ["check-health", "--project", "p", "--env", "e"],
        )

    assert result.exit_code == 0
    assert "Received 308. Adjusting URL" in result.stdout
    args, _ = mock_requests.get.call_args
    assert args[0] == "http://url/"
