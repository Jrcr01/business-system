"""Google Drive for the thermal statement QR (Thermal Statement Printing, build step 2, 2026-09-27).

The A4 statement PDF of each statement version is uploaded to ONE dedicated Drive folder, the file (never the folder) is shared
"anyone with the link can view", and that link is what the slip's QR encodes. When a newer version is printed, the old file's public
sharing is removed -- the file itself stays, privately, for the audit history. Files are never overwritten: every upload is a new file.

Access is the narrow "drive.file" permission: the app can only see and change files it created itself, nothing else in the owner's Drive.
Login files stay out of the project and git (db_location.drive_dir(), normally C:\\AP_Data\\drive):
  * credentials.json  -- the OAuth client the owner downloads once from Google Cloud (see drive_connect.bat)
  * token.json        -- written by drive_connect.bat after the owner signs in once; refreshed automatically after that
The folder id is kept per database in the settings table (key drive_statement_folder_id), so the test app and the live app use separate
folders ("... (TEST)" for the sandbox).
"""
from __future__ import annotations

import io
import os
import sqlite3

import db_location

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "Slick's AP - statement copies (QR)"
FOLDER_SETTING = "drive_statement_folder_id"


class DriveNotConnected(Exception):
    """Drive has not been set up on this laptop yet (no credentials / no sign-in) -- run drive_connect.bat once."""


class DriveError(Exception):
    """An upload / sharing call failed. The print must be aborted (no slip without a working QR)."""


def credentials_path() -> str:
    return os.path.join(db_location.drive_dir(), "credentials.json")


def token_path() -> str:
    return os.path.join(db_location.drive_dir(), "token.json")


def is_connected() -> bool:
    return os.path.exists(token_path())


def _load_credentials():
    """Saved sign-in, refreshed if expired (and saved again). Raises DriveNotConnected when there is none."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not os.path.exists(token_path()):
        raise DriveNotConnected("Google Drive is not connected yet. Double-click drive_connect.bat once to sign in.")
    creds = Credentials.from_authorized_user_file(token_path(), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:                                     # revoked / expired refresh token, no internet
                raise DriveError(f"Could not refresh the Google sign-in ({e}). Check the internet connection, or run drive_connect.bat again.") from e
            with open(token_path(), "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        else:
            raise DriveNotConnected("The Google sign-in is no longer valid. Run drive_connect.bat again.")
    return creds


def connect_interactive() -> str:
    """One-time sign-in (drive_connect.bat): opens the browser, the owner signs in and allows access, and the token is saved.
    Returns the signed-in account's email."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if not os.path.exists(credentials_path()):
        raise DriveNotConnected(f"credentials.json not found in {db_location.drive_dir()}.")
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path(), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(db_location.drive_dir(), exist_ok=True)
    with open(token_path(), "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    about = build("drive", "v3", credentials=creds, cache_discovery=False).about().get(fields="user(emailAddress)").execute()
    return about["user"]["emailAddress"]


class GoogleDrive:
    """The real Drive client. Tests use a fake with the same five methods."""

    def __init__(self):
        from googleapiclient.discovery import build

        self._svc = build("drive", "v3", credentials=_load_credentials(), cache_discovery=False)

    def _run(self, request):
        try:
            return request.execute(num_retries=2)
        except Exception as e:                                         # HttpError, timeouts, no internet
            raise DriveError(f"Google Drive: {e}") from e

    def create_folder(self, name: str) -> str:
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        return self._run(self._svc.files().create(body=meta, fields="id"))["id"]

    def folder_exists(self, folder_id: str) -> bool:
        try:
            f = self._run(self._svc.files().get(fileId=folder_id, fields="id, trashed"))
            return not f.get("trashed", False)
        except DriveError:
            return False

    def upload_pdf(self, data: bytes, name: str, folder_id: str) -> tuple[str, str]:
        """Always a NEW file (never overwrites). Returns (file id, link)."""
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf", resumable=False)
        f = self._run(self._svc.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id, webViewLink"))
        return f["id"], f["webViewLink"]

    def share_public(self, file_id: str) -> None:
        """This one file: anyone with the link can view (no sign-in). The folder is never shared."""
        self._run(self._svc.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader", "allowFileDiscovery": False}))

    def revoke_public(self, file_id: str) -> None:
        """Remove the anyone-with-link permission; the file stays, visible only to the owner."""
        perms = self._run(self._svc.permissions().list(fileId=file_id, fields="permissions(id, type)")).get("permissions", [])
        for p in perms:
            if p["type"] == "anyone":
                self._run(self._svc.permissions().delete(fileId=file_id, permissionId=p["id"]))


def statement_folder_id(drive, sandbox: bool) -> str:
    """This database's Drive folder: created on first use (by the app, so drive.file can reach it) and remembered in settings.
    Re-created if it was deleted / trashed in Drive."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (FOLDER_SETTING,)).fetchone()
    if row and row[0] and drive.folder_exists(row[0]):
        return row[0]
    folder_id = drive.create_folder(FOLDER_NAME + (" (TEST)" if sandbox else ""))
    with sqlite3.connect(DB_PATH) as conn:
        if row:
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (folder_id, FOLDER_SETTING))
        else:
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, 'thermal_printer', ?)",
                         (FOLDER_SETTING, folder_id, "Google Drive folder for the A4 statement copies behind the thermal slip QR codes"))
    return folder_id
