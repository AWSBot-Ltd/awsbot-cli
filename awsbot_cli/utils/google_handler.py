import json
import os

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .logger import get_logger

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsClient:
    def __init__(self):
        self.creds = None
        # Path to the OAuth Client ID you downloaded
        self.client_secret_file = os.environ.get(
            "GOOGLE_CLIENT_SECRET", "client_secret.json"
        )
        # Path where we will save your login token automatically
        self.token_file = "token.json"

        self._authenticate()
        self.client = gspread.authorize(self.creds)

    def _authenticate(self):
        """Handles the browser-based login flow."""
        # 1. Load existing token if we have logged in before
        if os.path.exists(self.token_file):
            try:
                self.creds = Credentials.from_authorized_user_file(
                    self.token_file, SCOPES
                )
            except Exception as e:
                logger.warning(f"Invalid token file, re-authenticating: {e}")

        # 2. If no valid credentials, let's log in
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                logger.info("Refreshing expired Google token...")
                self.creds.refresh(Request())
            else:
                logger.info("Initiating browser login...")
                # This launches the local server to catch the auth callback
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secret_file, SCOPES
                )
                self.creds = flow.run_local_server(port=0)

            # 3. Save the credentials for next time
            with open(self.token_file, "w") as token:
                token.write(self.creds.to_json())
                logger.info("Token saved for future runs.")

    def create_or_update_sheet(self, title, data, headers, share_with=None):
        # ... (This method stays exactly the same as before) ...
        # Note: You don't usually need 'share_with' anymore since YOU own the file!
        try:
            try:
                sh = self.client.open(title)
                logger.info(f"Opened existing sheet: {title}")
            except gspread.SpreadsheetNotFound:
                sh = self.client.create(title)
                logger.info(f"Created new sheet: {title}")

            worksheet = sh.get_worksheet(0)
            worksheet.clear()
            payload = [headers] + data
            worksheet.update(payload)
            worksheet.format("A1:Z1", {"textFormat": {"bold": True}})
            return sh.url
        except Exception as e:
            logger.error(f"Error updating sheet: {e}")
            raise
