import datetime

from awsbot_cli.utils.google_handler import GoogleSheetsClient
from awsbot_cli.utils.logger import get_logger, print_formatted_output

logger = get_logger(__name__)


def publish_report(
    rows: list,
    headers: list,
    title_prefix: str,
    share_email: str = None,
    local_only: bool = False,
):
    """
    Handles formatting data into a table and optionally uploading to Google Sheets.
    """
    # 1. Print Local Table
    print("\n=== REPORT DATA ===")
    # Convert list-of-lists to list-of-dicts for the printer
    table_data = [dict(zip(headers, row)) for row in rows]
    print_formatted_output(table_data, headers=headers)

    # 2. Handle Storage
    if local_only:
        logger.info("Local mode enabled. Skipping Google Sheets upload.")
        return

    sheet_title = f"{title_prefix}_{datetime.date.today()}"

    try:
        gs = GoogleSheetsClient()
        url = gs.create_or_update_sheet(
            sheet_title, rows, headers, share_with=share_email
        )
        print("\n✅ Uploaded to Google Sheets")
        print(f"📄 URL: {url}")
    except Exception as e:
        logger.error(f"Upload failed: {e}")
