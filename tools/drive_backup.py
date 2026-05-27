"""
tools/drive_backup.py — Google Drive Daily Backup
PT Cahaya Woodchar International — PRD v2.1

Backs up crm.db to Google Drive daily at 02:30.
Uses same OAuth2 credentials as Gmail API (credentials.json + token.pickle).

Creates a "PT Cahaya Woodchar Backups" folder in Google Drive.
Keeps last 30 backup files, auto-deletes older ones.

File naming: crm_backup_YYYY-MM-DD.db

Usage:
  from tools.drive_backup import DriveBackup
  backup = DriveBackup()
  backup.run()   # called by scheduler daily at 02:30
"""

import os
import io
import logging
import pickle
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "db" / "crm.db"
DRIVE_BACKUP_FOLDER = "PT Cahaya Woodchar Backups"
MAX_BACKUPS_TO_KEEP = 30
TOKEN_FILE = Path(__file__).parent.parent / "token.pickle"
CREDS_FILE = Path(__file__).parent.parent / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.readonly",  # already authorized
]


class DriveBackup:
    def __init__(self):
        self.enabled = os.getenv("DRIVE_BACKUP_ENABLED", "false").lower() == "true"
        self._service = None

    def _get_service(self):
        """Build Google Drive API service using existing OAuth token."""
        if self._service:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None
            if TOKEN_FILE.exists():
                with open(TOKEN_FILE, "rb") as f:
                    creds = pickle.load(f)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not CREDS_FILE.exists():
                        raise FileNotFoundError(
                            f"credentials.json not found at {CREDS_FILE}. "
                            "Download from Google Cloud Console."
                        )
                    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(TOKEN_FILE, "wb") as f:
                    pickle.dump(creds, f)

            self._service = build("drive", "v3", credentials=creds)
            logger.info("Google Drive service initialized")
            return self._service

        except Exception as e:
            logger.error(f"Failed to initialize Drive service: {e}")
            raise

    def _get_or_create_folder(self, service) -> str:
        """Get or create the backup folder in Google Drive. Returns folder ID."""
        query = (
            f"name='{DRIVE_BACKUP_FOLDER}' and "
            "mimeType='application/vnd.google-apps.folder' and "
            "trashed=false"
        )
        results = service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])

        if files:
            folder_id = files[0]["id"]
            logger.debug(f"Drive backup folder found: {folder_id}")
            return folder_id

        # Create folder
        metadata = {
            "name": DRIVE_BACKUP_FOLDER,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]
        logger.info(f"Drive backup folder created: {folder_id}")
        return folder_id

    def _upload_db(self, service, folder_id: str, filename: str) -> str:
        """Upload crm.db to Drive. Returns file ID."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise ImportError("google-api-python-client not installed. Run: pip install google-api-python-client")

        if not DB_PATH.exists():
            raise FileNotFoundError(f"Database not found at {DB_PATH}")

        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        media = MediaFileUpload(str(DB_PATH), mimetype="application/octet-stream", resumable=True)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size"
        ).execute()

        file_id = uploaded["id"]
        size_kb = int(uploaded.get("size", 0)) // 1024
        logger.info(f"Backup uploaded: {filename} ({size_kb} KB) → Drive ID: {file_id}")
        return file_id

    def _cleanup_old_backups(self, service, folder_id: str):
        """Delete backups older than MAX_BACKUPS_TO_KEEP days."""
        query = f"'{folder_id}' in parents and name contains 'crm_backup_' and trashed=false"
        results = service.files().list(
            q=query,
            fields="files(id, name, createdTime)",
            orderBy="createdTime asc"
        ).execute()
        files = results.get("files", [])

        if len(files) <= MAX_BACKUPS_TO_KEEP:
            logger.debug(f"Backup count {len(files)} ≤ {MAX_BACKUPS_TO_KEEP}, no cleanup needed")
            return

        to_delete = files[: len(files) - MAX_BACKUPS_TO_KEEP]
        for f in to_delete:
            try:
                service.files().delete(fileId=f["id"]).execute()
                logger.info(f"Deleted old backup: {f['name']}")
            except Exception as e:
                logger.warning(f"Could not delete old backup {f['name']}: {e}")

    def run(self):
        """
        Main backup routine. Called daily by scheduler at 02:30.
        Skips silently if DRIVE_BACKUP_ENABLED=false.
        """
        if not self.enabled:
            logger.debug("Drive backup disabled (DRIVE_BACKUP_ENABLED=false), skipping")
            return

        filename = f"crm_backup_{date.today().isoformat()}.db"
        logger.info(f"Drive backup starting: {filename}")

        try:
            service = self._get_service()
            folder_id = self._get_or_create_folder(service)
            file_id = self._upload_db(service, folder_id, filename)
            self._cleanup_old_backups(service, folder_id)

            # Log success to health_log
            self._log_backup(filename, file_id, success=True)

            # Telegram confirmation
            try:
                from tools.telegram_tool import send_message
                send_message(f"☁️ Drive backup successful: `{filename}`")
            except Exception:
                pass

            logger.info(f"Drive backup complete: {filename}")
            return file_id

        except Exception as e:
            logger.error(f"Drive backup failed: {e}")
            self._log_backup(filename, None, success=False, error=str(e))

            try:
                from tools.telegram_tool import send_message
                send_message(f"⚠️ Drive backup FAILED: {str(e)[:200]}")
            except Exception:
                pass
            return None

    def _log_backup(self, filename: str, file_id, success: bool, error: str = ""):
        """Log backup result to health_log table."""
        try:
            from db.database import get_conn
            status = "OK" if success else "ERROR"
            details = f"file={filename} drive_id={file_id} error={error}"
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO health_log (check_time, component, status, details)
                       VALUES (datetime('now'), 'drive_backup', ?, ?)""",
                    (status, details)
                )
        except Exception as e:
            logger.error(f"Failed to log backup result: {e}")
