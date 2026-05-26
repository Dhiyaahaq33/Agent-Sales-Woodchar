"""Gmail SMTP sender and Gmail API inbox reader."""
import base64
import logging
import pickle
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GMAIL_API_OK = True
except ImportError:
    _GMAIL_API_OK = False
    logger.warning("Gmail API libraries missing — inbox monitoring disabled.")

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

_UNSUBSCRIBE_FOOTER = (
    "\n\n---\nTo unsubscribe from our communications, reply with "
    "'UNSUBSCRIBE' in the subject line."
)


def send_email(to_email: str, subject: str, body: str,
               attachment_path: str = None) -> str:
    """Send via Gmail SMTP. Returns Message-ID string."""
    msg = MIMEMultipart()
    msg["From"] = f"{config.SENDER_NAME} <{config.GMAIL_ADDRESS}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body + _UNSUBSCRIBE_FOOTER, "plain"))

    if attachment_path and Path(attachment_path).exists():
        with open(attachment_path, "rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{Path(attachment_path).name}"',
        )
        msg.attach(part)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        server.sendmail(config.GMAIL_ADDRESS, to_email, msg.as_string())

    logger.info("Sent → %s | %s", to_email, subject)
    return msg.get("Message-ID", "")


def get_gmail_service():
    """Return an authenticated Gmail API service object."""
    if not _GMAIL_API_OK:
        raise RuntimeError("Gmail API libraries not installed.")

    token_path = Path("token.pickle")
    creds = None

    if token_path.exists():
        with open(token_path, "rb") as fh:
            creds = pickle.load(fh)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_JSON, _SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as fh:
            pickle.dump(creds, fh)

    return build("gmail", "v1", credentials=creds)


def get_unread_replies(known_thread_ids: set) -> list[dict]:
    """Fetch unread inbox messages that belong to threads we started."""
    try:
        service = get_gmail_service()
        results = service.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=100
        ).execute()
        replies = []
        for ref in results.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="full"
            ).execute()
            thread_id = msg.get("threadId")
            if thread_id not in known_thread_ids:
                continue
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            replies.append({
                "message_id": msg["id"],
                "thread_id": thread_id,
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "body": _extract_body(msg["payload"]),
            })
            service.users().messages().modify(
                userId="me", id=msg["id"],
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        return replies
    except Exception as exc:
        logger.error("Gmail inbox fetch failed: %s", exc)
        return []


def _extract_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""
