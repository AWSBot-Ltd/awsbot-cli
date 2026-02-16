import boto3
import pytest
from moto import mock_aws
from awsbot_cli.utils.s3 import append_lifecycle_rule, resolve_buckets


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
