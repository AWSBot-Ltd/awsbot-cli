import re
import time
from datetime import datetime, timezone

from botocore.exceptions import ClientError

# API calls that destroy/dispose of data or media, for disposal-audit evidence.
# ponytail: fixed list of common destructive events, not the full AWS API surface;
# add more (e.g. DeleteCluster, DeleteFunction) if an auditor asks for a resource
# type not covered here.
DESTRUCTIVE_EVENT_NAMES = [
    "DeleteBucket",
    "DeleteObject",
    "DeleteObjects",
    "DeleteVolume",
    "DeleteDBInstance",
    "DeleteDBCluster",
    "DeleteCertificate",
    "TerminateInstances",
    "DeleteTable",
    "DeleteFileSystem",
    "DeleteSnapshot",
]


def check_trail_data_events(client):
    """
    Lists trails and reports, per trail, whether S3 object-level (data) events
    are logged. Without that, DeleteObject calls won't show up in lookup_events.
    Returns a list of {"name", "s3_bucket", "s3_data_events_enabled"} dicts.
    """
    trails = client.describe_trails().get("trailList", [])
    results = []
    for trail in trails:
        name = trail.get("Name")
        s3_data_events = False
        try:
            selectors = client.get_event_selectors(TrailName=name)
        except ClientError:
            selectors = {}

        for sel in selectors.get("EventSelectors", []):
            for resource in sel.get("DataResources", []):
                if resource.get("Type") == "AWS::S3::Object":
                    s3_data_events = True

        for sel in selectors.get("AdvancedEventSelectors", []):
            fields = {
                f.get("Field"): f.get("Equals", [])
                for f in sel.get("FieldSelectors", [])
            }
            if "Data" in fields.get(
                "eventCategory", []
            ) and "AWS::S3::Object" in fields.get("resources.type", []):
                s3_data_events = True

        results.append(
            {
                "name": name,
                "s3_bucket": trail.get("S3BucketName"),
                "s3_key_prefix": trail.get("S3KeyPrefix"),
                "s3_data_events_enabled": s3_data_events,
                "is_organization_trail": trail.get("IsOrganizationTrail", False),
            }
        )
    return results


def discover_organization_id(s3_client, bucket, key_prefix=None):
    """
    Organization trails deliver logs under AWSLogs/{org-id}/{account-id}/...
    instead of AWSLogs/{account-id}/... . Discovers the org-id folder by
    listing common prefixes under the bucket's AWSLogs/ path, rather than
    requiring organizations:DescribeOrganization (which member accounts may
    not have). Returns None if no org-id-shaped folder is found.
    """
    prefix = f"{key_prefix}/AWSLogs/" if key_prefix else "AWSLogs/"
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")
    for common_prefix in response.get("CommonPrefixes", []):
        name = common_prefix.get("Prefix", "").rstrip("/").split("/")[-1]
        if re.match(r"^o-[a-z0-9]+$", name):
            return name
    return None


def find_deletion_events(client, start_time, end_time, event_names=None):
    """
    Searches CloudTrail Event History for the given event names within
    [start_time, end_time] (tz-aware datetimes). One EventName per
    lookup_events call is an API limitation, so this loops and aggregates.
    Returns events sorted by EventTime, deduplicated by EventId.
    """
    event_names = event_names or DESTRUCTIVE_EVENT_NAMES
    by_id = {}
    for event_name in event_names:
        next_token = None
        while True:
            kwargs = {
                "StartTime": start_time,
                "EndTime": end_time,
                "LookupAttributes": [
                    {"AttributeKey": "EventName", "AttributeValue": event_name}
                ],
            }
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.lookup_events(**kwargs)
            for evt in response.get("Events", []):
                by_id[evt["EventId"]] = evt
            next_token = response.get("NextToken")
            if not next_token:
                break

    return sorted(by_id.values(), key=lambda e: e.get("EventTime"))


# --- Athena fallback for ranges outside the ~90-day Event History window ---
# Standard AWS-documented CloudTrail Athena table schema.
_CLOUDTRAIL_TABLE_DDL = """
CREATE EXTERNAL TABLE {database}.{table} (
    eventversion STRING,
    useridentity STRUCT<
        type: STRING,
        principalid: STRING,
        arn: STRING,
        accountid: STRING,
        invokedby: STRING,
        accesskeyid: STRING,
        username: STRING,
        sessioncontext: STRUCT<
            attributes: STRUCT<
                mfaauthenticated: STRING,
                creationdate: STRING>,
            sessionissuer: STRUCT<
                type: STRING,
                principalid: STRING,
                arn: STRING,
                accountid: STRING,
                username: STRING>>>,
    eventtime STRING,
    eventsource STRING,
    eventname STRING,
    awsregion STRING,
    sourceipaddress STRING,
    useragent STRING,
    errorcode STRING,
    errormessage STRING,
    requestparameters STRING,
    responseelements STRING,
    additionaleventdata STRING,
    requestid STRING,
    eventid STRING,
    resources ARRAY<STRUCT<
        arn: STRING,
        accountid: STRING,
        type: STRING>>,
    eventtype STRING,
    apiversion STRING,
    readonly STRING,
    recipientaccountid STRING,
    serviceeventdetails STRING,
    sharedeventid STRING,
    vpcendpointid STRING
)
ROW FORMAT SERDE 'org.apache.hive.hcatalog.data.JsonSerDe'
STORED AS INPUTFORMAT 'com.amazon.emr.cloudtrail.CloudTrailInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION '{s3_location}'
""".strip()


def run_athena_query(
    client, query, output_location, workgroup="primary", poll_seconds=1, timeout=300
):
    """
    Runs a query to completion and returns its rows as a list of dicts
    keyed by column name. Raises RuntimeError on a failed/cancelled query,
    TimeoutError if it doesn't finish within `timeout` seconds.
    """
    result_config = {"OutputLocation": output_location} if output_location else {}
    start = client.start_query_execution(
        QueryString=query,
        ResultConfiguration=result_config,
        WorkGroup=workgroup,
    )
    query_id = start["QueryExecutionId"]

    elapsed = 0
    while True:
        status = client.get_query_execution(QueryExecutionId=query_id)[
            "QueryExecution"
        ]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "")
            raise RuntimeError(f"Athena query failed ({state}): {reason}")
        if elapsed >= timeout:
            raise TimeoutError(
                f"Athena query {query_id} did not finish within {timeout}s"
            )
        time.sleep(poll_seconds)
        elapsed += poll_seconds

    rows = []
    columns = None
    next_token = None
    while True:
        kwargs = {"QueryExecutionId": query_id}
        if next_token:
            kwargs["NextToken"] = next_token
        result = client.get_query_results(**kwargs)
        result_rows = result.get("ResultSet", {}).get("Rows", [])
        if columns is None:
            if not result_rows:
                break
            columns = [c.get("VarCharValue", "") for c in result_rows[0]["Data"]]
            data_rows = result_rows[1:]
        else:
            data_rows = result_rows
        for r in data_rows:
            values = [c.get("VarCharValue") for c in r["Data"]]
            rows.append(dict(zip(columns, values)))
        next_token = result.get("NextToken")
        if not next_token:
            break

    return rows


def ensure_cloudtrail_athena_table(
    client, database, table, s3_location, output_location, workgroup="primary"
):
    """
    Creates the Glue database if needed, then (re)defines the CloudTrail
    table pointing at s3_location. Always redefines the table (cheap
    metadata-only op) rather than skipping if it already exists, so a
    changed S3 path always takes effect instead of silently querying a
    stale location.
    """
    run_athena_query(
        client, f"CREATE DATABASE IF NOT EXISTS {database}", output_location, workgroup
    )
    run_athena_query(
        client, f"DROP TABLE IF EXISTS {database}.{table}", output_location, workgroup
    )
    ddl = _CLOUDTRAIL_TABLE_DDL.format(
        database=database, table=table, s3_location=s3_location
    )
    run_athena_query(client, ddl, output_location, workgroup)


def find_deletion_events_athena(
    client,
    database,
    table,
    output_location,
    start_time,
    end_time,
    event_names=None,
    workgroup="primary",
):
    """
    Athena equivalent of find_deletion_events, for date ranges outside the
    ~90-day Event History window. Queries the raw CloudTrail logs in S3 via
    the table set up by ensure_cloudtrail_athena_table. Returns events in
    the same shape as find_deletion_events (EventId/EventName/EventTime/
    Username/Resources) so callers don't need to branch on the source.
    """
    event_names = event_names or DESTRUCTIVE_EVENT_NAMES
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    names_sql = ", ".join(f"'{n}'" for n in event_names)

    query = f"""
SELECT
    eventtime,
    eventname,
    eventid,
    COALESCE(useridentity.username, useridentity.arn) AS actor,
    array_join(transform(resources, x -> COALESCE(x.arn, '')), ', ') AS resource_arns
FROM {database}.{table}
WHERE eventtime BETWEEN '{start_iso}' AND '{end_iso}'
  AND eventname IN ({names_sql})
ORDER BY eventtime
""".strip()

    rows = run_athena_query(client, query, output_location, workgroup)

    events = []
    for row in rows:
        ts_str = row.get("eventtime", "") or ""
        try:
            event_time = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            event_time = ts_str
        resource_arns = row.get("resource_arns") or ""
        events.append(
            {
                "EventId": row.get("eventid", ""),
                "EventName": row.get("eventname", ""),
                "EventTime": event_time,
                "Username": row.get("actor") or "",
                "Resources": [{"ResourceName": resource_arns}] if resource_arns else [],
            }
        )
    return events


def resolve_athena_output_location(client, workgroup, explicit=None):
    """Returns `explicit` if given, else the workgroup's configured output location, else None."""
    if explicit:
        return explicit
    try:
        wg = client.get_work_group(WorkGroup=workgroup)
    except ClientError:
        return None
    return (
        wg.get("WorkGroup", {})
        .get("Configuration", {})
        .get("ResultConfiguration", {})
        .get("OutputLocation")
    )
