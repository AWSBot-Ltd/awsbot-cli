from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from awsbot_cli.utils.ec2 import (
    list_instances,
    security_groups_open_to_internet,
    ssm_enrichment,
)


def _paginator(pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


# --- list_instances ---


def test_list_instances_no_reservations():
    client = MagicMock()
    client.get_paginator.return_value = _paginator([{"Reservations": []}])

    assert list_instances(client) == []


def test_list_instances_tagged_and_untagged():
    launch_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    pages = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-tagged",
                            "InstanceType": "t3.micro",
                            "State": {"Name": "running"},
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "PrivateIpAddress": "10.0.0.1",
                            "PublicIpAddress": "1.2.3.4",
                            "LaunchTime": launch_time,
                            "Tags": [{"Key": "Name", "Value": "web-1"}],
                            "PlatformDetails": "Linux/UNIX",
                            "MetadataOptions": {"HttpTokens": "required"},
                            "IamInstanceProfile": {
                                "Arn": "arn:aws:iam::123456789012:instance-profile/web-role"
                            },
                            "SecurityGroups": [
                                {"GroupId": "sg-1", "GroupName": "web-sg"},
                                {"GroupId": "sg-2", "GroupName": "ssh-sg"},
                            ],
                        },
                        {
                            "InstanceId": "i-untagged",
                            "InstanceType": "t3.small",
                            "State": {"Name": "stopped"},
                            "Placement": {"AvailabilityZone": "us-east-1b"},
                            "LaunchTime": launch_time,
                            "Tags": [],
                        },
                    ]
                }
            ]
        }
    ]
    client = MagicMock()
    client.get_paginator.return_value = _paginator(pages)

    result = list_instances(client)

    assert result[0] == {
        "Instance ID": "i-tagged",
        "Name": "web-1",
        "Type": "t3.micro",
        "State": "running",
        "AZ": "us-east-1a",
        "Private IP": "10.0.0.1",
        "Public IP": "1.2.3.4",
        "Launch Time": "2026-01-02 03:04:05",
        "OS": "Linux/UNIX",
        "IMDSv2 Enforced": "Yes",
        "IAM Instance Profile": "web-role",
        "Security Groups": "web-sg, ssh-sg",
        "_sg_ids": ["sg-1", "sg-2"],
    }
    assert result[1]["Name"] == "-"
    assert result[1]["Private IP"] == "-"
    assert result[1]["Public IP"] == "-"
    assert result[1]["OS"] == "-"
    assert result[1]["IMDSv2 Enforced"] == "No"
    assert result[1]["IAM Instance Profile"] == "-"
    assert result[1]["Security Groups"] == "-"
    assert result[1]["_sg_ids"] == []


# --- security_groups_open_to_internet ---


def test_security_groups_open_to_internet_empty_input_skips_api_call():
    client = MagicMock()

    assert security_groups_open_to_internet(client, []) == {}
    client.get_paginator.assert_not_called()


def test_security_groups_open_to_internet_ipv4_and_ipv6_and_restricted():
    client = MagicMock()
    client.get_paginator.return_value = _paginator(
        [
            {
                "SecurityGroups": [
                    {
                        "GroupId": "sg-open-v4",
                        "IpPermissions": [{"IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
                    },
                    {
                        "GroupId": "sg-open-v6",
                        "IpPermissions": [{"Ipv6Ranges": [{"CidrIpv6": "::/0"}]}],
                    },
                    {
                        "GroupId": "sg-restricted",
                        "IpPermissions": [{"IpRanges": [{"CidrIp": "10.0.0.0/24"}]}],
                    },
                ]
            }
        ]
    )

    result = security_groups_open_to_internet(
        client, ["sg-open-v4", "sg-open-v6", "sg-restricted"]
    )

    assert result == {"sg-open-v4": True, "sg-open-v6": True, "sg-restricted": False}


def test_security_groups_open_to_internet_chunks_large_input():
    client = MagicMock()
    client.get_paginator.return_value = _paginator([{"SecurityGroups": []}])
    ids = [f"sg-{i}" for i in range(250)]

    security_groups_open_to_internet(client, ids)

    assert client.get_paginator.return_value.paginate.call_count == 2


# --- ssm_enrichment ---


def test_ssm_enrichment_empty_input():
    ssm_client = MagicMock()

    assert ssm_enrichment(ssm_client, []) == {}
    ssm_client.get_paginator.assert_not_called()


def test_ssm_enrichment_managed_and_unmanaged():
    ssm_client = MagicMock()
    info_paginator = _paginator(
        [
            {
                "InstanceInformationList": [
                    {
                        "InstanceId": "i-managed",
                        "PlatformName": "Ubuntu",
                        "PlatformVersion": "22.04",
                    }
                ]
            }
        ]
    )
    patch_paginator = _paginator(
        [
            {
                "InstancePatchStates": [
                    {"InstanceId": "i-managed", "PatchComplianceStatus": "COMPLIANT"}
                ]
            }
        ]
    )
    paginators = {
        "describe_instance_information": info_paginator,
        "describe_instance_patch_states": patch_paginator,
    }
    ssm_client.get_paginator.side_effect = lambda op: paginators[op]

    result = ssm_enrichment(ssm_client, ["i-managed", "i-unmanaged"])

    assert result == {
        "i-managed": {
            "PlatformName": "Ubuntu",
            "PlatformVersion": "22.04",
            "PatchComplianceStatus": "COMPLIANT",
        }
    }


def test_ssm_enrichment_chunks_patch_state_calls():
    ssm_client = MagicMock()
    ids = [f"i-{i}" for i in range(75)]
    info_paginator = _paginator(
        [
            {
                "InstanceInformationList": [
                    {"InstanceId": i, "PlatformName": "Linux", "PlatformVersion": "1"}
                    for i in ids
                ]
            }
        ]
    )
    patch_paginator = _paginator([{"InstancePatchStates": []}])
    paginators = {
        "describe_instance_information": info_paginator,
        "describe_instance_patch_states": patch_paginator,
    }
    ssm_client.get_paginator.side_effect = lambda op: paginators[op]

    ssm_enrichment(ssm_client, ids)

    assert patch_paginator.paginate.call_count == 2


def test_ssm_enrichment_propagates_client_error():
    ssm_client = MagicMock()
    ssm_client.get_paginator.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}},
        "DescribeInstanceInformation",
    )

    with pytest.raises(ClientError):
        ssm_enrichment(ssm_client, ["i-1"])
