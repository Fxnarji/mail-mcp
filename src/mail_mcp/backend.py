"""Mailbox backend interface + fake in-memory implementation for the frontend spike.

The real IMAP backend will implement the same `Backend` protocol; the tool
surface in server.py must never touch anything beyond it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Mail:
    uid: str
    folder: str
    sender: str
    subject: str
    date: str
    body: str
    flags: set[str] = field(default_factory=set)
    processed: bool = False  # spike stand-in for the custom IMAP keyword
    message_id: str = ""  # RFC Message-ID, used for reply threading


class Backend(Protocol):
    def list_folders(self) -> list[str]: ...
    def list_mails(self, folder: str) -> list[Mail]: ...
    def get_mail(self, uid: str) -> Mail | None: ...
    def next_unprocessed(self) -> Mail | None: ...
    def mark_processed(self, uid: str) -> None: ...
    def resolve_folder(self, name: str) -> tuple[str, bool]: ...
    def move_mails(self, uids: list[str], folder: str) -> list[str]: ...
    def set_flag(self, uids: list[str], flag: str, value: bool) -> list[str]: ...
    def delete_mails(self, uids: list[str]) -> list[str]: ...
    def search_mails(self, query: str, folder: str | None) -> list[Mail]: ...
    def save_draft(self, body: str, reply_to: Mail | None, subject: str | None, to: str | None) -> str: ...


class FakeBackend:
    """In-memory mailbox with seed data, mirroring what real IMAP will do."""

    SPECIAL = {"drafts": "Drafts", "trash": "Trash"}

    def __init__(self) -> None:
        self._folders: list[str] = ["INBOX", "Archive", "Invoices", "Newsletters", "Drafts", "Trash"]
        self._next_uid = 100
        self._mails: dict[str, Mail] = {}
        for sender, subject, body in [
            ("billing@hetzner.com", "Invoice R0012345678", "Your invoice for July is attached. Amount due: 14.90 EUR."),
            ("newsletter@arstechnica.com", "This week in tech", "Top stories: quantum breakthroughs, GPU shortages..."),
            ("anna.k@example.org", "Dinner on Friday?", "Hey! Are you free Friday evening? Thinking 7pm at the usual place."),
            ("noreply@github.com", "[mail-MCP] CI failed on main", "Run #42 failed: test_backend.py::test_move"),
            ("updates@linkedin.com", "You appeared in 8 searches", "See who's looking at your profile..."),
        ]:
            self._add("INBOX", sender, subject, body)
        self._add("Archive", "old@example.com", "Last month's report", "Archived mail, should not show up as new.")

    def _add(self, folder: str, sender: str, subject: str, body: str) -> Mail:
        uid = str(self._next_uid)
        self._next_uid += 1
        mail = Mail(uid=uid, folder=folder, sender=sender, subject=subject, date="2026-07-18", body=body)
        self._mails[uid] = mail
        return mail

    # --- reading -------------------------------------------------------
    def list_folders(self) -> list[str]:
        return list(self._folders)

    def list_mails(self, folder: str) -> list[Mail]:
        resolved, _ = self.resolve_folder(folder)
        return [m for m in self._mails.values() if m.folder == resolved]

    def get_mail(self, uid: str) -> Mail | None:
        return self._mails.get(uid)

    def next_unprocessed(self) -> Mail | None:
        for m in self._mails.values():
            if m.folder == "INBOX" and not m.processed:
                return m
        return None

    def mark_processed(self, uid: str) -> None:
        if uid in self._mails:
            self._mails[uid].processed = True

    # --- writing -------------------------------------------------------
    def resolve_folder(self, name: str) -> tuple[str, bool]:
        """Loose-match name against existing folders; create if no match.

        Returns (canonical_name, created).
        """
        want = name.strip().casefold()
        for f in self._folders:
            if f.casefold() == want:
                return f, False
        # loose match: singular/plural and prefix, e.g. "newsletter" -> "Newsletters"
        for f in self._folders:
            fc = f.casefold()
            if fc.startswith(want) or want.startswith(fc):
                return f, False
        clean = name.strip()
        self._folders.append(clean)
        return clean, True

    def move_mails(self, uids: list[str], folder: str) -> list[str]:
        resolved, _ = self.resolve_folder(folder)
        moved = []
        for uid in uids:
            m = self._mails.get(uid)
            if m:
                m.folder = resolved
                moved.append(uid)
        return moved

    def set_flag(self, uids: list[str], flag: str, value: bool) -> list[str]:
        changed = []
        for uid in uids:
            m = self._mails.get(uid)
            if m:
                (m.flags.add if value else m.flags.discard)(flag)
                changed.append(uid)
        return changed

    def delete_mails(self, uids: list[str]) -> list[str]:
        return self.move_mails(uids, self.SPECIAL["trash"])

    def search_mails(self, query: str, folder: str | None = None) -> list[Mail]:
        q = query.casefold()
        scope = self.list_mails(folder) if folder else list(self._mails.values())
        return [m for m in scope if q in m.subject.casefold() or q in m.sender.casefold() or q in m.body.casefold()]

    def save_draft(self, body: str, reply_to: Mail | None = None, subject: str | None = None, to: str | None = None) -> str:
        if reply_to is not None:
            to = reply_to.sender
            subject = f"Re: {reply_to.subject}"
        draft = self._add(self.SPECIAL["drafts"], "me", subject or "(no subject)", body)
        draft.flags.add("Draft")
        return draft.uid
