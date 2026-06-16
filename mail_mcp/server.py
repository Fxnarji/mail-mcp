"""Mail MCP server for Hermes.

Exposes a deliberately small, read-and-organize-only mail toolset over MCP:
list folders, list messages, read a message, move a message, and create a
draft. There is intentionally NO delete and NO send tool -- those operations
are simply absent from the interface the model ever sees.

The backend is chosen at startup via the MAIL_BACKEND env var:
  demo  -> in-memory canned messages (default; needs no IMAP account at all)
  imap  -> a real IMAP account (see .env.example; use a BURNER account)
"""
from __future__ import annotations

import os
from dataclasses import asdict

from fastmcp import FastMCP

from .backends import DemoBackend
from .demo_data import load_demo_messages


def _make_backend():
    kind = os.environ.get("MAIL_BACKEND", "demo").lower()
    if kind == "imap":
        # imported lazily so demo mode never needs imap creds present
        from .backends import ImapBackend
        return ImapBackend()
    
    if kind == "demo":
        return DemoBackend(load_demo_messages())
    
    if kind == "himalaya":
        from .backends import HimalayaBackend
        return HimalayaBackend()


backend = _make_backend()

# The name here is what shows up to the MCP client (Hermes).
mcp = FastMCP("mail")


# NOTE: each tool's docstring + type hints ARE the interface the model sees.
# FastMCP turns the signature into the input schema and the docstring into the
# tool description, so write them like a function contract for the agent.

@mcp.tool
def list_folders() -> list[str]:
    """List the mailbox folders available in the account."""
    folders = backend.list_folders()
    return folders


@mcp.tool
def list_messages(folder: str = "INBOX", limit: int = 20) -> list[dict]:
    """List the most recent messages in a folder, newest first.

    Returns metadata only (id, sender, subject, date, unread, snippet).
    Use get_message to read a message's full body.
    """
    messages = backend.list_messages(folder=folder, limit=limit)
    return [asdict(m) for m in messages]


@mcp.tool
def get_message(message_id: str) -> dict:
    """Read a single message in full, including its body, by its id."""
    message = backend.get_message(message_id)
    return asdict(message)


@mcp.tool
def move_message(message_id: str, dest_folder: str) -> dict:
    """Move a message to another folder.

    Cannot move messages into Trash / Deleted Items / Junk -- those
    destinations are blocked by design.

    moving messages into non-existing folders will create the folder
    """
    result = backend.move_message(message_id, dest_folder)
    return result


@mcp.tool
def create_draft(to: str, subject: str, body: str) -> dict:
    """Create a draft email in the Drafts folder. This does NOT send anything."""
    draft = backend.create_draft(to=to, subject=subject, body=body)
    return draft

@mcp.tool
def flag_message(message_id: str) -> dict:
    """Mark a message as needing attention. Does not move or otherwise change it."""
    return backend.flag_message(message_id)

@mcp.tool
def unflag_message(message_id: str) -> dict:
    """Clear the attention flag from a message."""
    return backend.unflag_message(message_id)

@mcp.tool
def create_summary(body: str) -> dict:
    """Create a summary of the given text."""
    return backend.create_summary(body=body)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
