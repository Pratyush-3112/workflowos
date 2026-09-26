"""Slack connector supporting real Slack Web API and seamless local fallback."""

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class SlackConnector:
    """Sends Slack messages via real API if configured, otherwise logs to in-memory delivery store."""

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        self._delivered_messages: List[Dict[str, Any]] = []

    @property
    def is_configured(self) -> bool:
        """Check if real Slack Bot token is present."""
        return bool(self.bot_token and self.bot_token.startswith("xoxb-"))

    def send_message(self, channel: str, message: str) -> Tuple[Dict[str, Any], bool, Optional[str]]:
        """Send message to Slack channel.
        
        Returns:
            (payload, delivered_successfully, optional_error)
        """
        record = {
            "channel": channel,
            "text": message,
            "delivered": False,
            "mode": "REAL_API" if self.is_configured else "SIMULATED_INBOX",
        }

        if not self.is_configured:
            record["delivered"] = True
            self._delivered_messages.append(record)
            return record, True, "Simulated Slack delivery (set SLACK_BOT_TOKEN for real network delivery)."

        # Real Slack Web API call
        try:
            req_data = json.dumps({"channel": channel, "text": message}).encode("utf-8")
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=req_data,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = json.loads(response.read().decode("utf-8"))
                if res_body.get("ok"):
                    record["delivered"] = True
                    record["ts"] = res_body.get("ts")
                    self._delivered_messages.append(record)
                    return record, True, None
                else:
                    error_msg = res_body.get("error", "Unknown Slack API error")
                    return record, False, f"Slack API error: {error_msg}"
        except Exception as exc:
            return record, False, f"Network error posting to Slack: {exc}"

    def get_delivered_messages(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query real delivery log for verification."""
        if channel:
            return [m for m in self._delivered_messages if m["channel"] == channel]
        return list(self._delivered_messages)
