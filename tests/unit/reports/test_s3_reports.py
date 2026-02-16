# import pytest
# import boto3
# import datetime
# from botocore.exceptions import ClientError
# from unittest.mock import MagicMock, patch
#
# from awsbot_cli.reports.s3 import get_bucket_size, get_bucket_lifecycle, get_aws_billing_details
#
#
# @pytest.fixture
# def mock_session():
#     session = MagicMock(spec=boto3.Session)
#
#     # Ensure the client returned is a Mock we can configure
#     def side_effect(service, **kwargs):
#         m = MagicMock(name=f"mock_{service}_client")
#         return m
#
#     session.client.side_effect = side_effect
#     return session
#
#
# # --- 1. CloudWatch Size Tests ---
#
# def test_get_bucket_size_success(mock_session):
#     cw_client = mock_session.client("cloudwatch")
#     fixed_now = datetime.datetime(2026, 2, 16, 12, 0, 0)
#
#     with patch("awsbot_cli.reports.s3.datetime") as mock_dt:
#         mock_dt.utcnow.return_value = fixed_now
#         mock_dt.timedelta = datetime.timedelta
#
#         # CloudWatch metrics usually need to match the filter exactly.
#         # If your function uses a specific Stat, ensure the mock provides it.
#         cw_client.get_metric_statistics.return_value = {
#             "Datapoints": [{"Maximum": 1073741824.0}]
#         }
#
#         size = get_bucket_size("test-bucket", "us-east-1", mock_session)
#         assert size == 1073741824
#
#
# # --- 2. S3 Lifecycle Tests ---
#
# def test_get_bucket_lifecycle_success(mock_session):
#     s3_client = mock_session.client("s3")
#     # Explicitly return the dict the S3 client would return
#     s3_client.get_bucket_lifecycle_configuration.return_value = {
#         "Rules": [{"ID": "TestRule", "Status": "Enabled"}]
#     }
#
#     rules = get_bucket_lifecycle("test-bucket", mock_session)
#
#     # Check that it's a list and has the item
#     assert isinstance(rules, list)
#     assert len(rules) == 1
#     assert rules[0]["ID"] == "TestRule"
#
#
# def test_get_bucket_lifecycle_not_found(mock_session):
#     s3_client = mock_session.client("s3")
#
#     error_response = {"Error": {"Code": "NoSuchBucketLifecycle", "Message": "..."}}
#     s3_client.get_bucket_lifecycle_configuration.side_effect = ClientError(
#         error_response, "GetBucketLifecycleConfiguration"
#     )
#
#     rules = get_bucket_lifecycle("test-bucket", mock_session)
#
#     # If your function returns "Error", this test will fail until you
#     # update the function to return [] on NoSuchBucketLifecycle.
#     assert rules == []
#
#
# # --- 3. Billing Tests ---
#
# def test_get_aws_billing_details_actuals(mock_session):
#     ce_client = mock_session.client("ce")
#     # Simpler result set to avoid aggregation logic issues in tests
#     ce_client.get_cost_and_usage.return_value = {
#         "ResultsByTime": [{
#             "Groups": [{"Metrics": {"UnblendedCost": {"Amount": "10.0"}}}]
#         }]
#     }
#
#     results = get_aws_billing_details(mock_session, forecast=False)
#     assert len(results) > 0
#     # Use float comparison if necessary
#     assert float(results[0]["amount"]) == 10.0
