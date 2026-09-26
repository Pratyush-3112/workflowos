"""Shared Google OAuth2 authentication module for Gmail and Google Sheets.

Loads OAuth credentials from credentials.json and caches authorization
tokens in token.json with automatic refresh handling.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

GOOGLE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
]

DEFAULT_CREDENTIALS_FILE = Path("credentials.json")
DEFAULT_TOKEN_FILE = Path("token.json")


class GoogleAuthError(Exception):
    """Raised when Google OAuth credentials cannot be loaded or authenticated."""


def get_google_credentials(
    credentials_path: Path | str = DEFAULT_CREDENTIALS_FILE,
    token_path: Path | str = DEFAULT_TOKEN_FILE,
    scopes: Optional[list[str]] = None,
) -> Credentials:
    """Load or acquire Google OAuth2 user credentials.
    
    If token_path exists and is valid, loads cached token.
    If token is expired, refreshes it automatically.
    If no valid token exists, opens a local web server for one-time user authorization.
    """
    credentials_file = Path(credentials_path)
    token_file = Path(token_path)
    target_scopes = scopes or GOOGLE_SCOPES

    creds: Optional[Credentials] = None

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), target_scopes)
        except Exception as e:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not credentials_file.exists():
                raise GoogleAuthError(
                    f"Google OAuth credentials file not found at: '{credentials_file.resolve()}'. "
                    "Download your Desktop OAuth client credentials from Google Cloud Console "
                    "and place it at 'credentials.json' in the project root."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_file),
                target_scopes,
            )
            creds = flow.run_local_server(port=0)

        # Cache refreshed/acquired credentials to token.json
        if creds:
            token_file.write_text(creds.to_json())

    return creds
