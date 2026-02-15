import json
import subprocess
import sys

import boto3


class SSMConnector:
    def __init__(self, profile=None):
        self.session = boto3.Session(profile_name=profile)
        self.ssm_client = self.session.client("ssm")

    def start_interactive_session(self, instance_id):
        try:
            # 1. Initiate the session via Boto3
            response = self.ssm_client.start_session(Target=instance_id)

            # 2. Prepare the parameters for the session-manager-plugin
            # The plugin expects a JSON string of the start_session response
            session_data = json.dumps(response)

            # 3. Use subprocess to run the session-manager-plugin
            # This allows the plugin to take over the terminal's STDIN/STDOUT
            subprocess.check_call(
                [
                    "session-manager-plugin",
                    session_data,
                    self.session.region_name,
                    "StartSession",
                    "",  # profile name (usually empty when passing session data directly)
                    json.dumps({"Target": instance_id}),
                    self.ssm_client.meta.endpoint_url,
                ]
            )
        except Exception as e:
            print(f"Failed to connect to {instance_id}: {e}")
            sys.exit(1)
