import json
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from awsbot_cli.utils.s3 import (
    append_lifecycle_rule,
    check_ssl_policy,
    merge_ssl_policy,
    resolve_buckets,
)


@pytest.fixture
def s3_client():
    """Moto-mocked S3 client."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        yield client


# --- Tests for resolve_buckets ---


def test_resolve_buckets_single(s3_client):
    """Verify it returns a single bucket if it exists."""
    s3_client.create_bucket(Bucket="target-bucket")

    result = resolve_buckets(s3_client, bucket="target-bucket")
    assert result == ["target-bucket"]


def test_resolve_buckets_missing(s3_client):
    """Verify it returns empty list for non-existent bucket."""
    result = resolve_buckets(s3_client, bucket="ghost-bucket")
    assert result == []


def test_resolve_buckets_filter(s3_client):
    """Verify keyword filtering logic."""
    buckets = ["prod-data", "prod-logs", "dev-test"]
    for b in buckets:
        s3_client.create_bucket(Bucket=b)

    result = resolve_buckets(s3_client, filter_keyword="prod")
    assert len(result) == 2
    assert "prod-data" in result
    assert "prod-logs" in result


# --- Tests for append_lifecycle_rule ---


def test_append_lifecycle_rule_new_config(s3_client):
    """Test creating a lifecycle config on a bucket that has none."""
    bucket_name = "clean-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    new_rule = {
        "ID": "MoveToGlacier",
        "Status": "Enabled",
        "Filter": {"Prefix": "logs/"},
        "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}],
    }

    success = append_lifecycle_rule(s3_client, bucket_name, new_rule)
    assert success is True

    # Verify via API
    config = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
    assert config["Rules"][0]["ID"] == "MoveToGlacier"


def test_append_lifecycle_rule_deduplication(s3_client):
    """Verify it skips if a rule with the same ID already exists."""
    bucket_name = "existing-rule-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    rule = {"ID": "DuplicateID", "Status": "Enabled", "Filter": {"Prefix": ""}}

    # Apply first time
    s3_client.put_bucket_lifecycle_configuration(
        Bucket=bucket_name, LifecycleConfiguration={"Rules": [rule]}
    )

    # Try appending the same ID
    success = append_lifecycle_rule(s3_client, bucket_name, rule)
    assert success is True  # Function returns True (it "successfully" handled the skip)

    # Verify no second rule was added
    config = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
    assert len(config["Rules"]) == 1


def test_append_lifecycle_rule_merging(s3_client):
    """Ensure existing rules are preserved when a new one is added."""
    bucket_name = "merge-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    existing_rule = {"ID": "Rule1", "Status": "Enabled", "Filter": {"Prefix": "1/"}}
    s3_client.put_bucket_lifecycle_configuration(
        Bucket=bucket_name, LifecycleConfiguration={"Rules": [existing_rule]}
    )

    new_rule = {"ID": "Rule2", "Status": "Enabled", "Filter": {"Prefix": "2/"}}
    append_lifecycle_rule(s3_client, bucket_name, new_rule)

    config = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
    assert len(config["Rules"]) == 2
    assert config["Rules"][0]["ID"] == "Rule1"
    assert config["Rules"][1]["ID"] == "Rule2"


# --- Tests for merge_ssl_policy ---


def test_merge_ssl_policy_create_new(s3_client):
    """No existing policy: a new one is created with the deny statement."""
    bucket_name = "no-policy-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    result = merge_ssl_policy(s3_client, bucket_name)
    assert result == "CREATED"

    policy = json.loads(s3_client.get_bucket_policy(Bucket=bucket_name)["Policy"])
    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 1
    assert policy["Statement"][0]["Sid"] == "AllowSSLRequestsOnly"
    assert policy["Statement"][0]["Resource"] == [
        f"arn:aws:s3:::{bucket_name}",
        f"arn:aws:s3:::{bucket_name}/*",
    ]


def test_merge_ssl_policy_skip_existing(s3_client):
    """Existing AllowSSLRequestsOnly statement: skipped, policy untouched."""
    bucket_name = "already-secure-bucket"
    s3_client.create_bucket(Bucket=bucket_name)
    existing_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowSSLRequestsOnly",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket_name}"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }
        ],
    }
    s3_client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(existing_policy))

    result = merge_ssl_policy(s3_client, bucket_name)
    assert result == "SKIPPED"

    policy = json.loads(s3_client.get_bucket_policy(Bucket=bucket_name)["Policy"])
    assert len(policy["Statement"]) == 1


def test_merge_ssl_policy_modify_preserves_existing(s3_client):
    """Existing unrelated statements are preserved, not overwritten."""
    bucket_name = "merge-policy-bucket"
    s3_client.create_bucket(Bucket=bucket_name)
    existing_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "OtherAllow",
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
            }
        ],
    }
    s3_client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(existing_policy))

    result = merge_ssl_policy(s3_client, bucket_name)
    assert result == "MODIFIED"

    policy = json.loads(s3_client.get_bucket_policy(Bucket=bucket_name)["Policy"])
    sids = {s["Sid"] for s in policy["Statement"]}
    assert sids == {"OtherAllow", "AllowSSLRequestsOnly"}


def test_merge_ssl_policy_dry_run_no_changes(s3_client):
    """dry_run reports the planned status without calling put_bucket_policy."""
    bucket_name = "dry-run-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    result = merge_ssl_policy(s3_client, bucket_name, dry_run=True)
    assert result == "CREATED"

    with pytest.raises(ClientError) as exc_info:
        s3_client.get_bucket_policy(Bucket=bucket_name)
    assert exc_info.value.response["Error"]["Code"] == "NoSuchBucketPolicy"


def test_merge_ssl_policy_invalid_json():
    """Malformed existing policy JSON is reported as an error, not raised."""
    client = MagicMock()
    client.get_bucket_policy.return_value = {"Policy": "{not valid json"}

    result = merge_ssl_policy(client, "broken-policy-bucket")
    assert result == "ERROR"
    client.put_bucket_policy.assert_not_called()


def test_merge_ssl_policy_access_denied():
    """AccessDenied fetching the policy is reported as an error, not raised."""
    client = MagicMock()
    client.get_bucket_policy.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetBucketPolicy"
    )

    result = merge_ssl_policy(client, "locked-bucket")
    assert result == "ERROR"
    client.put_bucket_policy.assert_not_called()


# --- Tests for check_ssl_policy ---


def test_check_ssl_policy_compliant(s3_client):
    """Bucket with the AllowSSLRequestsOnly statement is reported compliant."""
    bucket_name = "compliant-bucket"
    s3_client.create_bucket(Bucket=bucket_name)
    merge_ssl_policy(s3_client, bucket_name)

    assert check_ssl_policy(s3_client, bucket_name) == "COMPLIANT"


def test_check_ssl_policy_non_compliant(s3_client):
    """Bucket with no policy at all is reported non-compliant, read-only."""
    bucket_name = "non-compliant-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    assert check_ssl_policy(s3_client, bucket_name) == "NON_COMPLIANT"

    with pytest.raises(ClientError) as exc_info:
        s3_client.get_bucket_policy(Bucket=bucket_name)
    assert exc_info.value.response["Error"]["Code"] == "NoSuchBucketPolicy"


def test_check_ssl_policy_error():
    """Errors fetching the policy are reported, not raised."""
    client = MagicMock()
    client.get_bucket_policy.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetBucketPolicy"
    )

    assert check_ssl_policy(client, "locked-bucket") == "ERROR"
