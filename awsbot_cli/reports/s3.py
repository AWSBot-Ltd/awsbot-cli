from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

# Try importing the utilities.
try:
    from awsbot_cli.utils.logger import print_formatted_output
except ImportError:

    def print_formatted_output(data, headers):
        print(f"{headers}")
        for row in data:
            print(row)


try:
    from awsbot_cli.utils.reporter import publish_report
except ImportError:
    publish_report = None


def get_bucket_size(
    bucket_name: str, region: str, session: boto3.Session, cache_data: dict = None
) -> int:
    """Queries CloudWatch for the BucketSizeBytes metric."""
    if cache_data is not None and bucket_name in cache_data:
        cached_item = cache_data[bucket_name]
        cached_ts = cached_item.get("timestamp", 0)
        if (datetime.utcnow().timestamp() - cached_ts) < 86400:
            return cached_item.get("size", 0)

    target_region = region if region else "us-east-1"
    cw = session.client("cloudwatch", region_name=target_region)

    size = 0
    try:
        response = cw.get_metric_statistics(
            Namespace="AWS/S3",
            MetricName="BucketSizeBytes",
            Dimensions=[
                {"Name": "BucketName", "Value": bucket_name},
                {"Name": "StorageType", "Value": "StandardStorage"},
            ],
            StartTime=datetime.utcnow() - timedelta(days=2),
            EndTime=datetime.utcnow(),
            Period=86400,
            Statistics=["Maximum"],
        )
        datapoints = response.get("Datapoints", [])
        if datapoints:
            latest = sorted(datapoints, key=lambda x: x["Timestamp"], reverse=True)[0]
            size = int(latest["Maximum"])
    except Exception:
        pass

    if cache_data is not None:
        cache_data[bucket_name] = {
            "size": size,
            "timestamp": datetime.utcnow().timestamp(),
        }

    return size


def get_bucket_lifecycle(s3_client, bucket_name: str) -> str:
    """Checks if the bucket has lifecycle rules attached."""
    try:
        response = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        rules = response.get("Rules", [])
        return f"Yes ({len(rules)} rules)"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchLifecycleConfiguration":
            return "None"
        elif error_code == "AccessDenied":
            return "Access Denied"
        else:
            return f"Error: {error_code}"
    except Exception:
        return "Error"


def get_aws_billing_details(session: boto3.Session, forecast: bool = False) -> list:
    """Fetches S3 cost actuals or a monthly forecast from AWS Cost Explorer."""
    try:
        ce = session.client("ce")
        now = datetime.utcnow()

        # Actuals: 1st of month to today
        start_date = now.replace(day=1).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        if forecast:
            # Forecast requires a future end date (1st of next month)
            # and a start date that is today or later.
            start_date = now.strftime("%Y-%m-%d")
            next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
            end_date = next_month.strftime("%Y-%m-%d")

            response = ce.get_cost_forecast(
                TimePeriod={"Start": start_date, "End": end_date},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
                Filter={
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Simple Storage Service"],
                    }
                },
            )
            return [
                {
                    "type": "Forecasted Total",
                    "amount": float(response["Total"]["Amount"]),
                }
            ]

        # Fallback for 1st of the month actuals logic
        if start_date == end_date:
            start_date = (
                (now.replace(day=1) - timedelta(days=1))
                .replace(day=1)
                .strftime("%Y-%m-%d")
            )

        results = []
        next_token = None

        while True:
            kwargs = {
                "TimePeriod": {"Start": start_date, "End": end_date},
                "Granularity": "MONTHLY",
                "Filter": {
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Simple Storage Service"],
                    }
                },
                "GroupBy": [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
                "Metrics": ["UnblendedCost"],
            }
            if next_token:
                kwargs["NextPageToken"] = next_token

            response = ce.get_cost_and_usage(**kwargs)
            for time_period in response.get("ResultsByTime", []):
                for group in time_period.get("Groups", []):
                    usage_type = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if amount > 0.001:
                        results.append({"type": usage_type, "amount": amount})
            next_token = response.get("NextPageToken")
            if not next_token:
                break
        results.sort(key=lambda x: x["amount"], reverse=True)
        return results
    except Exception as e:
        print(f"Error fetching billing: {e}")
        return []
