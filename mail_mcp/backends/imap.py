"""IMAP backend: talks to a real IMAP account using stdlib (imaplib + email)."""
from __future__ import annotations

import os

from . import Message, _guard_dest


class ImapBackend:
    """Minimal IMAP backend (stdlib only). Connection-per-call for simplicity;
    swap in a pooled/persistent connection once you move past testing."""

    def __init__(self) -> None:
        self.host = os.environ["IMAP_HOST"]
        self.port = int(os.environ.get("IMAP_PORT", "993"))
        self.user = os.environ["IMAP_USER"]
        self.password = os.environ["IMAP_PASSWORD"]
        self.drafts_folder = os.environ.get("IMAP_DRAFTS_FOLDER", "Drafts")

    # -- helpers ----------------------------------------------------------
    def _connect(self):
        import imaplib
        imap = imaplib.IMAP4_SSL(self.host, self.port)
        imap.login(self.user, self.password)
        return imap

    @staticmethod
    def _decode(value) -> str:
        from email.header import make_header, decode_header
        if not value:
            return ""
        return str(make_header(decode_header(value)))

    @staticmethod
    def _split_id(message_id: str):
        folder, _, uid = message_id.rpartition(":")
        if not folder or not uid:
            raise ValueError(f"Malformed id {message_id!r} (expected 'FOLDER:UID')")
        return folder, uid

    @staticmethod
    def _plaintext_body(msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                disp = str(part.get("Content-Disposition", ""))
                if part.get_content_type() == "text/plain" and "attachment" not in disp:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
            return ""
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

    # -- interface --------------------------------------------------------
    def list_folders(self) -> list[str]:
        imap = self._connect()
        try:
            typ, data = imap.list()
            names = []
            for raw in data or []:
                line = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
                # crude: the folder name is the last quoted segment of the line
                names.append(line.split(' "')[-1].strip().strip('"'))
            return names
        finally:
            imap.logout()

    def list_messages(self, folder: str = "INBOX", limit: int = 20) -> list[Message]:
        import email
        imap = self._connect()
        try:
            imap.select(folder, readonly=True)  # readonly => doesn't set \Seen
            typ, data = imap.uid("search", None, "ALL")
            uids = data[0].split()[-limit:][::-1]  # newest first
            out: list[Message] = []
            for uid in uids:
                typ, md = imap.uid("fetch", uid, "(BODY.PEEK[HEADER] FLAGS)")
                if not md or not isinstance(md[0], tuple):
                    continue
                flags_blob = md[0][0].decode(errors="replace")  # contains FLAGS (...)
                msg = email.message_from_bytes(md[0][1])
                out.append(Message(
                    id=f"{folder}:{uid.decode()}",
                    folder=folder,
                    sender=self._decode(msg.get("From")),
                    subject=self._decode(msg.get("Subject")),
                    date=self._decode(msg.get("Date")),
                    unread="\\Seen" not in flags_blob,
                    snippet="",
                    body="",
                ))
            return out
        finally:
            imap.logout()

    def get_message(self, message_id: str) -> Message:
        import email
        folder, uid = self._split_id(message_id)
        imap = self._connect()
        try:
            imap.select(folder, readonly=True)
            typ, md = imap.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            if not md or not isinstance(md[0], tuple):
                raise ValueError(f"Could not fetch {message_id!r}")
            msg = email.message_from_bytes(md[0][1])
            body = self._plaintext_body(msg)
            return Message(
                id=message_id, folder=folder,
                sender=self._decode(msg.get("From")),
                subject=self._decode(msg.get("Subject")),
                date=self._decode(msg.get("Date")),
                unread=False, snippet=body[:120], body=body,
            )
        finally:
            imap.logout()

    def move_message(self, message_id: str, dest_folder: str) -> dict:
        _guard_dest(dest_folder)
        folder, uid = self._split_id(message_id)
        imap = self._connect()
        try:
            imap.select(folder)
            uid_b = uid.encode()
            # Preferred path: server-side UID MOVE (RFC 6851).
            try:
                typ, _ = imap.uid("MOVE", uid_b, dest_folder)
                if typ != "OK":
                    raise RuntimeError("MOVE unsupported")
            except Exception:
                # Fallback for servers without MOVE: copy then expunge the source
                # copy. This is the standard move fallback; there is still no
                # standalone delete tool exposed to the model.
                imap.uid("COPY", uid_b, dest_folder)
                imap.uid("STORE", uid_b, "+FLAGS", "(\\Deleted)")
                imap.expunge()
            return {"status": "moved", "id": message_id,
                    "from": folder, "to": dest_folder}
        finally:
            imap.logout()

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        import email.message
        import imaplib
        import time
        imap = self._connect()
        try:
            msg = email.message.EmailMessage()
            msg["From"] = self.user
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            # APPEND to Drafts with the \Draft flag. This writes a draft; it is
            # NOT an SMTP send. (There is no SMTP configured anywhere here.)
            imap.append(self.drafts_folder, "(\\Draft)",
                        imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            return {"status": "draft_created", "folder": self.drafts_folder,
                    "to": to, "subject": subject}
        finally:
            imap.logout()
