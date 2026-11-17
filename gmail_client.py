import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import re

def clean_email_html(raw_html: str) -> str:
    """Extracts readable text from HTML email content."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    # Remove script and style elements
    for s in soup(["script", "style"]):
        s.extract()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()

def strip_infomentor_email_boilerplate(text: str) -> str:
    """Removes standard repetitive InfoMentor header/footer boilerplate."""
    if not text:
        return ""
    # Strip: För att svara på detta meddelandet Klicka här. ( Om länken ovan inte fungerar... )
    text = re.sub(r"För att svara på detta meddelandet\s*Klicka här\.\s*\(.*?\)\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip footer: ... skickat via Infomentor
    text = re.sub(r".*?\s+skickat via Infomentor\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()

def decode_mime_header(header_val: Optional[str]) -> str:
    """Decodes MIME encoded header strings."""
    if not header_val:
        return ""
    parts = decode_header(header_val)
    decoded_fragments = []
    for raw_part, encoding in parts:
        if isinstance(raw_part, bytes):
            decoded_fragments.append(raw_part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded_fragments.append(str(raw_part))
    return " ".join(decoded_fragments).strip()

class GmailClient:
    def __init__(
        self,
        user: Optional[str] = None,
        password: Optional[str] = None,
        sender_filter: Optional[str] = None
    ):
        self.user = user or os.environ.get("GMAIL_USER")
        self.password = password or os.environ.get("GMAIL_APP_PASSWORD")
        self.sender_filter = sender_filter or os.environ.get("GMAIL_SENDER", "noreply-se@system.infomentor.se")
        self.imap_host = "imap.gmail.com"

    def is_configured(self) -> bool:
        """Returns True if Gmail credentials are provided."""
        return bool(self.user and self.password)

    def get_recent_emails(self, days_back: int = 14, max_emails: int = 10) -> List[Dict[str, Any]]:
        """
        Connects via IMAP and retrieves recent school emails matching sender_filter.
        """
        if not self.is_configured():
            return []

        emails = []
        try:
            # Login
            mail = imaplib.IMAP4_SSL(self.imap_host)
            mail.login(self.user, self.password)
            mail.select("INBOX", readonly=True)

            # Search query: SINCE date and FROM sender_filter
            since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
            search_query = f'(FROM "{self.sender_filter}" SINCE {since_date})'
            status, data = mail.search(None, search_query)

            if status != "OK" or not data or not data[0]:
                # Fallback to general sender search if SINCE is strict
                status, data = mail.search(None, f'(FROM "{self.sender_filter}")')

            if status == "OK" and data and data[0]:
                msg_ids = data[0].split()
                # Take the most recent max_emails
                selected_ids = msg_ids[-max_emails:]

                for mid in reversed(selected_ids):
                    res, msg_data = mail.fetch(mid, "(RFC822)")
                    if res != "OK":
                        continue

                    for part in msg_data:
                        if isinstance(part, tuple):
                            msg = email.message_from_bytes(part[1])
                            subject = decode_mime_header(msg.get("Subject", ""))
                            sender = decode_mime_header(msg.get("From", ""))
                            date_str = msg.get("Date", "")

                            # Extract body
                            body_text = ""
                            if msg.is_multipart():
                                for p in msg.walk():
                                    ctype = p.get_content_type()
                                    if ctype in ("text/plain", "text/html"):
                                        payload = p.get_payload(decode=True)
                                        if payload:
                                            # Try charset
                                            charset = p.get_content_charset() or "utf-8"
                                            try:
                                                decoded = payload.decode(charset, errors="replace")
                                            except Exception:
                                                decoded = payload.decode("latin-1", errors="replace")
                                            if ctype == "text/html":
                                                decoded = clean_email_html(decoded)
                                            body_text += f" {decoded}"
                            else:
                                payload = msg.get_payload(decode=True)
                                if payload:
                                    charset = msg.get_content_charset() or "utf-8"
                                    try:
                                        decoded = payload.decode(charset, errors="replace")
                                    except Exception:
                                        decoded = payload.decode("latin-1", errors="replace")
                                    if msg.get_content_type() == "text/html":
                                        decoded = clean_email_html(decoded)
                                    body_text = decoded

                            clean_body = strip_infomentor_email_boilerplate(re.sub(r"\s+", " ", body_text).strip())
                            clean_mid = mid.decode() if isinstance(mid, bytes) else str(mid)
                            emails.append({
                                "id": f"email_{clean_mid}",
                                "date": date_str,
                                "sender": sender,
                                "subject": subject,
                                "body": clean_body[:3000]
                            })

            mail.logout()
        except Exception as ex:
            print(f"   ⚠️ Failed to fetch school emails via Gmail IMAP: {ex}")

        return emails
