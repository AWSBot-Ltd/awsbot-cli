import datetime

import boto3


def get_monthly_cost_by_service():
    """
    Fetches costs for the last 30 days grouped by Service.
    """
    client = boto3.client("ce")

    # Calculate dates
    end = datetime.date.today()
    start = end - datetime.timedelta(days=30)

    # Cost Explorer API
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    data = []
    for result in response["ResultsByTime"]:
        for group in result["Groups"]:
            service_name = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amount > 0:  # Filter out $0 costs
                data.append(
                    [service_name, round(amount, 2), result["TimePeriod"]["Start"]]
                )

    # Sort by cost descending
    data.sort(key=lambda x: x[1], reverse=True)
    return data, ["Service", "Cost (USD)", "Month Start"]
