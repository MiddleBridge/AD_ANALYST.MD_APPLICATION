import os
import base64
import json
from pathlib import Path
from typing import Optional
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from storage.models import EmailData

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]

ALLOWED_SENDER = os.getenv("ALLOWED_SENDER", "dokuzu12@gmail.com")
PITCH_DECK_QUERY = os.getenv(
    "PITCH_DECK_QUERY",
    f"from:{ALLOWED_SENDER} has:attachment filename:pdf -label:Inovo/Screened",
)


class GmailClient:
    def __init__(self):
        self.credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
        self.token_path = os.getenv("GMAIL_TOKEN_PATH", "token.json")
        self.processed_label = os.getenv("GMAIL_PROCESSED_LABEL", "Inovo/Screened")
        self.needs_review_label = os.getenv("GMAIL_NEEDS_REVIEW_LABEL", "Inovo/NeedsReview")
        self.service = self._authenticate()
        self._label_id_cache: dict = {}

    def _authenticate(self):
        creds = None
        if Path(self.token_path).exists():
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            Path(self.token_path).write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds)

    def get_unread_pitchdecks(self) -> list[EmailData]:
        results = self.service.users().messages().list(
            userId="me", q=PITCH_DECK_QUERY, maxResults=20
        ).execute()

        messages = results.get("messages", [])
        email_data_list = []

        for msg_ref in messages:
            try:
                email_data = self._parse_message(msg_ref["id"])
                if email_data and email_data.sender_email.lower() == ALLOWED_SENDER.lower():
                    email_data_list.append(email_data)
                elif email_data:
                    print(f"[gmail] Skipping email from {email_data.sender_email} (not allowed sender)")
            except Exception as e:
                print(f"[gmail] Error parsing message {msg_ref['id']}: {e}")

        return email_data_list

    def get_message(self, message_id: str) -> Optional[EmailData]:
        """Fetch a specific message by id (ignores unread/label filters)."""
        try:
            return self._parse_message(message_id)
        except Exception as e:
            print(f"[gmail] Error fetching message {message_id}: {e}")
            return None

    def _parse_message(self, message_id: str) -> Optional[EmailData]:
        msg = self.service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        sender_raw = headers.get("from", "")
        sender_name, sender_email = parseaddr(sender_raw)
        subject = headers.get("subject", "(no subject)")
        date = headers.get("date", "")
        thread_id = msg.get("threadId", "")

        body = self._extract_body(msg["payload"])

        pdf_filename = None
        attachment_id = None
        has_pdf = False

        parts = self._get_all_parts(msg["payload"])
        for part in parts:
            mime = part.get("mimeType", "")
            filename = part.get("filename", "")
            if mime == "application/pdf" or filename.lower().endswith(".pdf"):
                has_pdf = True
                pdf_filename = filename
                body_data = part.get("body", {})
                attachment_id = body_data.get("attachmentId")
                break

        return EmailData(
            message_id=message_id,
            sender_email=sender_email,
            sender_name=sender_name or sender_email,
            subject=subject,
            body=body[:3000],
            date=date,
            has_pdf=has_pdf,
            pdf_filename=pdf_filename,
            attachment_id=attachment_id,
            thread_id=thread_id,
        )

    def _extract_body(self, payload: dict) -> str:
        parts = self._get_all_parts(payload)
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return _strip_html(html)
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    def _get_all_parts(self, payload: dict) -> list:
        parts = []
        if "parts" in payload:
            for part in payload["parts"]:
                parts.append(part)
                parts.extend(self._get_all_parts(part))
        else:
            parts.append(payload)
        return parts

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        att = self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        return base64.urlsafe_b64decode(att["data"])

    def mark_as_processed(self, message_id: str) -> None:
        label_id = self._get_or_create_label(self.processed_label)
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def mark_as_needs_review(self, message_id: str) -> None:
        label_id = self._get_or_create_label(self.needs_review_label)
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def create_draft(self, to: str, subject: str, body: str, thread_id: Optional[str] = None) -> str:
        import email.mime.text
        msg = email.mime.text.MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft_body: dict = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id
        draft = self.service.users().drafts().create(
            userId="me", body=draft_body
        ).execute()
        return draft["id"]

    def _get_or_create_label(self, label_name: str) -> str:
        if label_name in self._label_id_cache:
            return self._label_id_cache[label_name]

        labels = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for lbl in labels:
            if lbl["name"] == label_name:
                self._label_id_cache[label_name] = lbl["id"]
                return lbl["id"]

        new_label = self.service.users().labels().create(
            userId="me",
            body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        self._label_id_cache[label_name] = new_label["id"]
        return new_label["id"]


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
