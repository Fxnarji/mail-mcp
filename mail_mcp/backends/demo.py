"""Demo backend: in-memory, fully working, no network."""
from __future__ import annotations

from dataclasses import asdict

from . import Message, _guard_dest


class DemoBackend:
    """Fully functional in-memory backend. No network, no credentials."""

    def __init__(self, messages: list[Message]):
        self._messages: dict[str, Message] = {m.id: m for m in messages}
        self._draft_counter = 1000

    def list_folders(self) -> list[str]:
        return sorted({m.folder for m in self._messages.values()} | {"Drafts"})

    def list_messages(self, folder: str = "INBOX", limit: int = 20) -> list[Message]:
        msgs = [m for m in self._messages.values()
                if m.folder.lower() == folder.lower()]
        msgs.sort(key=lambda m: m.date, reverse=True)
        # listings carry the snippet but not the full body
        return [Message(**{**asdict(m), "body": ""}) for m in msgs[:limit]]

    def get_message(self, message_id: str) -> Message:
        m = self._messages.get(message_id)
        if m is None:
            raise ValueError(f"No message with id {message_id!r}")
        return m

    def move_message(self, message_id: str, dest_folder: str) -> dict:
        _guard_dest(dest_folder)
        m = self._messages.get(message_id)
        if m is None:
            raise ValueError(f"No message with id {message_id!r}")
        old, m.folder = m.folder, dest_folder
        return {"status": "moved", "id": message_id, "from": old, "to": dest_folder}

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        self._draft_counter += 1
        mid = f"Drafts:{self._draft_counter}"
        self._messages[mid] = Message(
            id=mid, folder="Drafts", sender="me", subject=subject,
            date="2026-06-15T12:00:00", unread=False,
            snippet=body[:80], body=body,
        )
        return {"status": "draft_created", "id": mid, "to": to, "subject": subject}
