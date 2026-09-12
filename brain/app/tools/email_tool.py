"""Email tools -- plain IMAP/SMTP with a Gmail App Password (stdlib only, no OAuth app
registration needed). Gives the agent read/search/send access to one mailbox."""
import email
import imaplib
import logging
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText

from .. import config

log = logging.getLogger("jarvis.email")


def _imap_connect() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST, config.EMAIL_IMAP_PORT)
    conn.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
    return conn


def _decode(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in parts
    )


def _extract_body(msg: email.message.Message, max_chars: int = 2000) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                charset = part.get_content_charset() or "utf-8"
                text = part.get_payload(decode=True).decode(charset, errors="replace")
                return text[:max_chars]
        return "(kein Text-Inhalt gefunden, evtl. nur HTML/Anhänge)"
    charset = msg.get_content_charset() or "utf-8"
    return msg.get_payload(decode=True).decode(charset, errors="replace")[:max_chars]


def list_unread_emails(max_results: int = 10) -> str:
    """List unread emails in the inbox (sender, subject, date, id) -- does not mark them as
    read. Use read_email with the id to get the full body of one of them.

    Args:
        max_results: max number of emails to list (default 10, most recent first).
    """
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)
        status, data = conn.search(None, "UNSEEN")
        ids = data[0].split()
        if not ids:
            return "Keine ungelesenen E-Mails."
        ids = ids[-max_results:][::-1]
        lines = []
        for eid in ids:
            _, msg_data = conn.fetch(eid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            header = email.message_from_bytes(msg_data[0][1])
            lines.append(
                f"id={eid.decode()} | Von: {_decode(header.get('From', ''))} | "
                f"Betreff: {_decode(header.get('Subject', ''))} | {header.get('Date', '')}"
            )
        conn.logout()
        return "\n".join(lines)
    except Exception as e:
        log.exception("list_unread_emails fehlgeschlagen")
        return f"Fehler beim Abrufen der E-Mails: {e}"


def read_email(email_id: str) -> str:
    """Read the full body of one email by its id (from list_unread_emails or search_emails).

    Args:
        email_id: the numeric id shown as "id=..." in list_unread_emails/search_emails output.
    """
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)
        _, msg_data = conn.fetch(email_id.encode(), "(BODY.PEEK[])")
        msg = email.message_from_bytes(msg_data[0][1])
        conn.logout()
        body = _extract_body(msg)
        return (
            f"Von: {_decode(msg.get('From', ''))}\n"
            f"Betreff: {_decode(msg.get('Subject', ''))}\n"
            f"Datum: {msg.get('Date', '')}\n\n{body}"
        )
    except Exception as e:
        log.exception("read_email fehlgeschlagen")
        return f"Fehler beim Lesen der E-Mail: {e}"


def search_emails(query: str, max_results: int = 10) -> str:
    """Search emails by subject or sender (IMAP text search across the whole inbox).

    Args:
        query: search text (matches subject and sender).
        max_results: max number of results (default 10, most recent first).
    """
    try:
        conn = _imap_connect()
        conn.select("INBOX", readonly=True)
        _, data = conn.search(None, f'(OR SUBJECT "{query}" FROM "{query}")')
        ids = data[0].split()
        if not ids:
            return "Keine Treffer."
        ids = ids[-max_results:][::-1]
        lines = []
        for eid in ids:
            _, msg_data = conn.fetch(eid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            header = email.message_from_bytes(msg_data[0][1])
            lines.append(
                f"id={eid.decode()} | Von: {_decode(header.get('From', ''))} | "
                f"Betreff: {_decode(header.get('Subject', ''))} | {header.get('Date', '')}"
            )
        conn.logout()
        return "\n".join(lines)
    except Exception as e:
        log.exception("search_emails fehlgeschlagen")
        return f"Fehler bei der E-Mail-Suche: {e}"


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from the user's own address.

    Args:
        to: recipient email address.
        subject: email subject line.
        body: plain-text email body.
    """
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = config.EMAIL_ADDRESS
        msg["To"] = to
        with smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT) as server:
            server.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
            server.send_message(msg)
        return f"E-Mail an {to} gesendet."
    except Exception as e:
        log.exception("send_email fehlgeschlagen")
        return f"Fehler beim Senden: {e}"
