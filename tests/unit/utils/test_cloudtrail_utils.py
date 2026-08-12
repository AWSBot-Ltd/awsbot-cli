from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from awsbot_cli.utils.cloudtrail import (
    check_trail_data_events,
    discover_organization_id,
    ensure_cloudtrail_athena_table,
    find_deletion_events,
    find_deletion_events_athena,
    resolve_athena_output_location,
    run_athena_query,
)


def _varchar_row(*values):
    return {"Data": [{"VarCharValue": v} for v in values]}


# --- Tests for check_trail_data_events ---


def test_check_trail_data_events_no_trails():
    client = MagicMock()
    client.describe_trails.return_value = {"trailList": []}

    assert check_trail_data_events(client) == []


def test_check_trail_data_events_legacy_selector_enabled():
    client = MagicMock()
    client.describe_trails.return_value = {
        "trailList": [{"Name": "main-trail", "S3BucketName": "logs-bucket"}]
    }
    client.get_event_selectors.return_value = {
        "EventSelectors": [
            {"DataResources": [{"Type": "AWS::S3::Object"}]},
        ]
    }

    result = check_trail_data_events(client)
    assert result == [
        {
            "name": "main-trail",
            "s3_bucket": "logs-bucket",
            "s3_key_prefix": None,
            "s3_data_events_enabled": True,
            "is_organization_trail": False,
        }
    ]


def test_check_trail_data_events_advanced_selector_enabled():
    client = MagicMock()
    client.describe_trails.return_value = {
        "trailList": [{"Name": "main-trail", "S3BucketName": "logs-bucket"}]
    }
    client.get_event_selectors.return_value = {
        "AdvancedEventSelectors": [
            {
                "FieldSelectors": [
                    {"Field": "eventCategory", "Equals": ["Data"]},
                    {"Field": "resources.type", "Equals": ["AWS::S3::Object"]},
                ]
            }
        ]
    }

    result = check_trail_data_events(client)
    assert result[0]["s3_data_events_enabled"] is True


def test_check_trail_data_events_disabled():
    client = MagicMock()
    client.describe_trails.return_value = {
        "trailList": [{"Name": "mgmt-only-trail", "S3BucketName": "logs-bucket"}]
    }
    client.get_event_selectors.return_value = {
        "EventSelectors": [{"DataResources": []}]
    }

    result = check_trail_data_events(client)
    assert result[0]["s3_data_events_enabled"] is False


# --- Tests for find_deletion_events ---


def test_find_deletion_events_aggregates_and_sorts():
    client = MagicMock()

    def lookup_events(**kwargs):
        event_name = kwargs["LookupAttributes"][0]["AttributeValue"]
        if event_name == "DeleteBucket":
            return {
                "Events": [
                    {
                        "EventId": "1",
                        "EventName": "DeleteBucket",
                        "EventTime": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "Username": "alice",
                        "Resources": [{"ResourceName": "old-bucket"}],
                    }
                ]
            }
        if event_name == "DeleteVolume":
            return {
                "Events": [
                    {
                        "EventId": "2",
                        "EventName": "DeleteVolume",
                        "EventTime": datetime(2026, 3, 20, tzinfo=timezone.utc),
                        "Username": "bob",
                        "Resources": [{"ResourceName": "vol-123"}],
                    }
                ]
            }
        return {"Events": []}

    client.lookup_events.side_effect = lookup_events

    events = find_deletion_events(
        client,
        datetime(2026, 3, 17, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
        event_names=["DeleteBucket", "DeleteVolume"],
    )

    assert [e["EventId"] for e in events] == ["2", "1"]  # sorted by EventTime


def test_find_deletion_events_paginates():
    client = MagicMock()
    client.lookup_events.side_effect = [
        {
            "Events": [
                {
                    "EventId": "1",
                    "EventName": "DeleteBucket",
                    "EventTime": datetime(2026, 4, 1, tzinfo=timezone.utc),
                }
            ],
            "NextToken": "page2",
        },
        {
            "Events": [
                {
                    "EventId": "2",
                    "EventName": "DeleteBucket",
                    "EventTime": datetime(2026, 4, 2, tzinfo=timezone.utc),
                }
            ]
        },
    ]

    events = find_deletion_events(
        client,
        datetime(2026, 3, 17, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
        event_names=["DeleteBucket"],
    )

    assert len(events) == 2
    assert client.lookup_events.call_count == 2


def test_find_deletion_events_no_results():
    client = MagicMock()
    client.lookup_events.return_value = {"Events": []}

    events = find_deletion_events(
        client,
        datetime(2026, 3, 17, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
        event_names=["DeleteBucket"],
    )

    assert events == []


# --- Tests for run_athena_query ---


def test_run_athena_query_succeeds_after_polling():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": "RUNNING"}}},
        {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
    ]
    client.get_query_results.return_value = {
        "ResultSet": {
            "Rows": [
                _varchar_row("eventtime", "eventname"),
                _varchar_row("2026-04-02T10:00:00Z", "DeleteBucket"),
            ]
        }
    }

    rows = run_athena_query(client, "SELECT 1", "s3://bucket/out/", poll_seconds=0)

    assert rows == [{"eventtime": "2026-04-02T10:00:00Z", "eventname": "DeleteBucket"}]
    assert client.get_query_execution.call_count == 2


def test_run_athena_query_paginates():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    client.get_query_results.side_effect = [
        {
            "ResultSet": {
                "Rows": [_varchar_row("eventname"), _varchar_row("DeleteBucket")]
            },
            "NextToken": "page2",
        },
        {"ResultSet": {"Rows": [_varchar_row("DeleteVolume")]}},
    ]

    rows = run_athena_query(client, "SELECT 1", "s3://bucket/out/", poll_seconds=0)

    assert rows == [{"eventname": "DeleteBucket"}, {"eventname": "DeleteVolume"}]


def test_run_athena_query_raises_on_failure():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {
            "Status": {"State": "FAILED", "StateChangeReason": "table not found"}
        }
    }

    with pytest.raises(RuntimeError, match="table not found"):
        run_athena_query(client, "SELECT 1", "s3://bucket/out/", poll_seconds=0)


def test_run_athena_query_raises_on_timeout():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "RUNNING"}}
    }

    with pytest.raises(TimeoutError):
        run_athena_query(
            client, "SELECT 1", "s3://bucket/out/", poll_seconds=0, timeout=0
        )


# --- Tests for find_deletion_events_athena ---


def test_find_deletion_events_athena_normalizes_rows():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    client.get_query_results.return_value = {
        "ResultSet": {
            "Rows": [
                _varchar_row(
                    "eventtime", "eventname", "eventid", "actor", "resource_arns"
                ),
                _varchar_row(
                    "2026-04-02T10:00:00Z",
                    "DeleteBucket",
                    "evt-1",
                    "alice",
                    "arn:aws:s3:::old-bucket",
                ),
            ]
        }
    }

    events = find_deletion_events_athena(
        client,
        "awsbot_audit",
        "cloudtrail_main",
        "s3://bucket/out/",
        datetime(2026, 3, 17, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
    )

    assert events == [
        {
            "EventId": "evt-1",
            "EventName": "DeleteBucket",
            "EventTime": datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc),
            "Username": "alice",
            "Resources": [{"ResourceName": "arn:aws:s3:::old-bucket"}],
        }
    ]


def test_find_deletion_events_athena_no_resources():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    client.get_query_results.return_value = {
        "ResultSet": {
            "Rows": [
                _varchar_row(
                    "eventtime", "eventname", "eventid", "actor", "resource_arns"
                ),
                _varchar_row(
                    "2026-04-02T10:00:00Z", "TerminateInstances", "evt-2", "bob", ""
                ),
            ]
        }
    }

    events = find_deletion_events_athena(
        client,
        "awsbot_audit",
        "cloudtrail_main",
        "s3://bucket/out/",
        datetime(2026, 3, 17, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
    )

    assert events[0]["Resources"] == []


# --- Tests for resolve_athena_output_location ---


def test_resolve_athena_output_location_explicit_wins():
    client = MagicMock()
    result = resolve_athena_output_location(
        client, "primary", explicit="s3://explicit/"
    )
    assert result == "s3://explicit/"
    client.get_work_group.assert_not_called()


def test_resolve_athena_output_location_falls_back_to_workgroup():
    client = MagicMock()
    client.get_work_group.return_value = {
        "WorkGroup": {
            "Configuration": {
                "ResultConfiguration": {"OutputLocation": "s3://from-workgroup/"}
            }
        }
    }

    result = resolve_athena_output_location(client, "primary", explicit=None)

    assert result == "s3://from-workgroup/"


def test_resolve_athena_output_location_none_when_unset():
    client = MagicMock()
    client.get_work_group.return_value = {"WorkGroup": {"Configuration": {}}}

    result = resolve_athena_output_location(client, "primary", explicit=None)

    assert result is None


# --- Tests for discover_organization_id ---


def test_discover_organization_id_found():
    client = MagicMock()
    client.list_objects_v2.return_value = {
        "CommonPrefixes": [{"Prefix": "AWSLogs/o-8s6ewnn3u5/"}]
    }

    org_id = discover_organization_id(client, "my-bucket")

    assert org_id == "o-8s6ewnn3u5"
    client.list_objects_v2.assert_called_once_with(
        Bucket="my-bucket", Prefix="AWSLogs/", Delimiter="/"
    )


def test_discover_organization_id_with_key_prefix():
    client = MagicMock()
    client.list_objects_v2.return_value = {
        "CommonPrefixes": [{"Prefix": "myprefix/AWSLogs/o-abc123/"}]
    }

    org_id = discover_organization_id(client, "my-bucket", key_prefix="myprefix")

    assert org_id == "o-abc123"
    client.list_objects_v2.assert_called_once_with(
        Bucket="my-bucket", Prefix="myprefix/AWSLogs/", Delimiter="/"
    )


def test_discover_organization_id_not_found():
    client = MagicMock()
    client.list_objects_v2.return_value = {
        "CommonPrefixes": [{"Prefix": "AWSLogs/123456789012/"}]
    }

    assert discover_organization_id(client, "my-bucket") is None


# --- Tests for ensure_cloudtrail_athena_table ---


def test_ensure_cloudtrail_athena_table_drops_before_recreating():
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    client.get_query_results.return_value = {"ResultSet": {"Rows": []}}

    ensure_cloudtrail_athena_table(
        client, "awsbot_audit", "cloudtrail_main", "s3://bucket/path/", "s3://out/"
    )

    queries = [
        c.kwargs["QueryString"] for c in client.start_query_execution.call_args_list
    ]
    assert any(q.startswith("CREATE DATABASE IF NOT EXISTS") for q in queries)
    assert any(
        q == "DROP TABLE IF EXISTS awsbot_audit.cloudtrail_main" for q in queries
    )
    assert any(
        "CREATE EXTERNAL TABLE awsbot_audit.cloudtrail_main" in q for q in queries
    )
