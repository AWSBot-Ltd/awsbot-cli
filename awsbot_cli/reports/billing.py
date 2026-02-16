import datetime
import boto3
from collections import defaultdict


def get_monthly_cost_by_service():
    """
    Fetches costs for the last 30 days grouped by Service.
    Used by the 'report' command.
    """
    client = boto3.client("ce")
    end = datetime.date.today()
    start = end - datetime.timedelta(days=30)

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
            if amount > 0:
                data.append(
                    [service_name, round(amount, 2), result["TimePeriod"]["Start"]]
                )

    data.sort(key=lambda x: x[1], reverse=True)
    return data, ["Service", "Cost (USD)", "Month Start"]


def get_billing_data(start_date=None, end_date=None, service_filter=None):
    """
    Flexible billing fetcher used by the 'show' command.
    Pivots data to show columns for each month + a Total row.
    """
    client = boto3.client("ce")

    # 1. Handle Dates
    if not end_date:
        end_dt = datetime.date.today()
        end_date = end_dt.isoformat()
    else:
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

    if not start_date:
        start_dt = end_dt - datetime.timedelta(days=90)
        start_date = start_dt.isoformat()

    # 2. Build Query
    query_args = {
        "TimePeriod": {"Start": start_date, "End": end_date},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }

    if service_filter:
        query_args["Filter"] = {
            "Dimensions": {"Key": "SERVICE", "Values": [service_filter]}
        }

    # 3. Fetch Data
    response = client.get_cost_and_usage(**query_args)

    # 4. Pivot Data & Calculate Totals
    service_map = defaultdict(lambda: {"total": 0.0})
    monthly_totals = defaultdict(float)  # Track totals per month column
    all_months = set()
    grand_total_spend = 0.0

    for result in response["ResultsByTime"]:
        start_str = result["TimePeriod"]["Start"]
        month_label = start_str[:7]
        all_months.add(month_label)

        for group in result["Groups"]:
            service_name = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])

            if amount > 0:
                grand_total_spend += amount
                # Add to service row
                service_map[service_name][month_label] = amount
                service_map[service_name]["total"] += amount
                # Add to column total
                monthly_totals[month_label] += amount

    # 5. Format Output
    sorted_months = sorted(list(all_months))
    headers = ["Service"] + sorted_months + ["Total"]

    rows = []
    for service_name, costs in service_map.items():
        row = {"Service": service_name}
        for month in sorted_months:
            amount = costs.get(month, 0.0)
            row[month] = f"${amount:.2f}" if amount > 0 else "-"
        row["Total"] = f"${costs['total']:.2f}"
        rows.append(row)

    # Sort by Total Cost descending
    rows.sort(key=lambda x: float(x["Total"].replace("$", "")), reverse=True)

    # 6. Append Total Row
    total_row = {"Service": "--- TOTAL ---"}
    for month in sorted_months:
        total_row[month] = f"${monthly_totals[month]:.2f}"
    total_row["Total"] = f"${grand_total_spend:.2f}"

    rows.append(total_row)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_spend": grand_total_spend,
        "data": rows,
        "headers": headers,
    }
