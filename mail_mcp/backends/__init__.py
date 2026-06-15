"""Mail backends for the MCP server."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Message:
    id: str          # opaque; for IMAP this is "FOLDER:UID"
    folder: str
    sender: str
    subject: str
    date: str
    unread: bool
    snippet: str
    body: str = ""


@dataclass
class EmailAttachment:
    filename: str
    content_type: str
    size: int


class MailBackend(Protocol):
    def list_folders(self) -> list[str]: ...
    def list_messages(self, folder: str = "INBOX", limit: int = 20) -> list[Message]: ...
    def get_message(self, message_id: str) -> Message: ...
    def move_message(self, message_id: str, dest_folder: str) -> dict: ...
    def create_draft(self, to: str, subject: str, body: str) -> dict: ...


# Destinations the move tool must never relocate a message into.
_BLOCKED_DEST = {"trash", "deleted", "deleted items", "bin", "junk", "spam"}


def _guard_dest(dest_folder: str) -> None:
    if dest_folder.strip().lower() in _BLOCKED_DEST:
        raise ValueError(
            f"Refusing to move to {dest_folder!r}: moving into a trash-like "
            "folder is disabled by design (use of delete-by-move is blocked)."
        )


from .demo import DemoBackend
from .imap import ImapBackend
from .himalaya import HimalayaBackend

__all__ = ["Message", "EmailAttachment", "MailBackend", "_guard_dest", "DemoBackend", "ImapBackend", "HimalayaBackend"]
