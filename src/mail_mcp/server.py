"""mail-mcp server: three agent-facing surfaces over one Backend.

Surface A (hermes default): sort_inbox        -- sampling-driven, one call total
Surface B (portable Mode 1): next_mail/sort_mail -- one call per mail
Surface C (Mode 2):          general mailbox tools

Which surface an agent sees is decided by the client config (hermes
`tools.include`), not by the server.
"""

from __future__ import annotations

import json
import re

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent

from .backend import Backend, FakeBackend, Mail

mcp = FastMCP("mail")

backend: Backend = FakeBackend()

# uid of the mail last served by next_mail(), awaiting a sort_mail() decision
_current_uid: str | None = None


def _format_mail(m: Mail, body_limit: int = 500) -> str:
    body = m.body if len(m.body) <= body_limit else m.body[:body_limit] + " [...]"
    return (
        f"From: {m.sender}\n"
        f"Subject: {m.subject}\n"
        f"Date: {m.date}\n"
        f"\n{body}"
    )


def _serve_next() -> str:
    global _current_uid
    m = backend.next_unprocessed()
    if m is None:
        _current_uid = None
        return "Inbox clear. No new mail to sort."
    _current_uid = m.uid
    folders = ", ".join(f for f in backend.list_folders() if f != "INBOX")
    return (
        f"=== New mail ===\n{_format_mail(m)}\n\n"
        f"Existing folders: {folders}\n"
        "Sort it: call sort_mail with a folder name (existing or new; 'INBOX' keeps it here) "
        "and optionally a reply text to save as a draft."
    )


# --------------------------------------------------------------------------
# Surface B: portable spoon-feeding loop
# --------------------------------------------------------------------------

@mcp.tool()
def next_mail() -> str:
    """Get the next unsorted mail from the inbox, together with the list of
    folders it could be moved to. After reading it, call sort_mail."""
    return _serve_next()


@mcp.tool()
def sort_mail(folder: str, response: str | None = None) -> str:
    """Sort the mail you just received from next_mail.

    folder: where to move it. Any name is accepted -- existing folders are
    matched case-insensitively, unknown names create a new folder.
    Use 'INBOX' to leave the mail where it is.
    response: optional reply text; it is saved as a draft (never sent).

    Returns confirmation plus the NEXT mail to sort, so you can keep calling
    sort_mail until the inbox is clear.
    """
    global _current_uid
    if _current_uid is None:
        return "No mail is pending a decision. Call next_mail first."
    mail = backend.get_mail(_current_uid)
    if mail is None:
        _current_uid = None
        return "The pending mail vanished. Call next_mail to continue."

    lines = [_apply_decision(mail, folder, response)]
    _current_uid = None
    lines.append("")
    lines.append(_serve_next())
    return "\n".join(lines)


def _apply_decision(mail: Mail, folder: str, response: str | None) -> str:
    resolved, created = backend.resolve_folder(folder)
    parts = []
    if resolved == "INBOX":
        backend.mark_processed(mail.uid)
        parts.append(f"Kept '{mail.subject}' in INBOX.")
    else:
        backend.move_mails([mail.uid], resolved)
        parts.append(f"Moved '{mail.subject}' to {resolved}" + (" (new folder)." if created else "."))
    if response:
        backend.save_draft(response, reply_to=mail)
        parts.append(f"Draft reply to {mail.sender} saved.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Surface A: sampling-driven, server does everything
# --------------------------------------------------------------------------

_SORT_PROMPT = """You are sorting one email into a folder.

{mail}

Existing folders: {folders}

Answer with ONLY a JSON object, no other text:
{{"folder": "<folder name, existing or new; INBOX to keep>", "reply": "<reply text, or null if no reply is needed>"}}

Only write a reply if the mail is personally addressed and clearly expects an answer."""


@mcp.tool()
async def sort_inbox(ctx: Context) -> str:
    """Sort all new mail in the inbox into folders automatically and draft
    replies where needed. Call this once; it processes every new mail and
    returns a summary report."""
    report: list[str] = []
    limit = 25  # spike guard: never loop unbounded
    while limit > 0:
        limit -= 1
        mail = backend.next_unprocessed()
        if mail is None:
            break
        prompt = _SORT_PROMPT.format(
            mail=_format_mail(mail),
            folders=", ".join(backend.list_folders()),
        )
        try:
            result = await ctx.session.create_message(
                messages=[SamplingMessage(role="user", content=TextContent(type="text", text=prompt))],
                max_tokens=400,
            )
        except Exception as exc:
            report.append(f"STOPPED: sampling unavailable ({exc}). Use next_mail/sort_mail instead.")
            break
        decision = _parse_decision(result.content.text if isinstance(result.content, TextContent) else "")
        if decision is None:
            backend.mark_processed(mail.uid)  # skip rather than loop forever on it
            report.append(f"SKIPPED '{mail.subject}': model answer was not valid JSON.")
            continue
        folder, reply = decision
        report.append(_apply_decision(mail, folder, reply))
    if not report:
        return "Inbox clear. Nothing to sort."
    return "Inbox sorted:\n" + "\n".join(f"- {line}" for line in report)


def _parse_decision(text: str) -> tuple[str, str | None] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        folder = data["folder"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(folder, str) or not folder.strip():
        return None
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = None
    return folder, reply


# --------------------------------------------------------------------------
# Surface C: Mode 2 general mailbox tools
# --------------------------------------------------------------------------

@mcp.tool()
def list_folders() -> str:
    """List all mail folders."""
    return "\n".join(backend.list_folders())


@mcp.tool()
def list_mails(folder: str = "INBOX") -> str:
    """List the mails in a folder (id, sender, subject)."""
    mails = backend.list_mails(folder)
    if not mails:
        return f"No mails in {folder}."
    return "\n".join(f"[{m.uid}] {m.sender} -- {m.subject} ({m.date})" for m in mails)


@mcp.tool()
def read_mail(mail_id: str) -> str:
    """Read a full mail by its id (as shown by list_mails or search_mails)."""
    m = backend.get_mail(mail_id)
    if m is None:
        return f"No mail with id {mail_id}."
    return f"[{m.uid}] in {m.folder}\n{_format_mail(m, body_limit=5000)}"


@mcp.tool()
def move_mails(mail_ids: list[str], folder: str) -> str:
    """Move one or more mails to a folder. Unknown folder names create a new folder."""
    resolved, created = backend.resolve_folder(folder)
    moved = backend.move_mails(mail_ids, resolved)
    note = " (new folder)" if created else ""
    return f"Moved {len(moved)} mail(s) to {resolved}{note}."


@mcp.tool()
def flag_mails(mail_ids: list[str], flag: str = "Flagged", value: bool = True) -> str:
    """Set or clear a flag on mails. Common flags: Flagged, Seen."""
    changed = backend.set_flag(mail_ids, flag, value)
    return f"{'Set' if value else 'Cleared'} {flag} on {len(changed)} mail(s)."


@mcp.tool()
def delete_mails(mail_ids: list[str]) -> str:
    """Delete mails (moves them to Trash)."""
    deleted = backend.delete_mails(mail_ids)
    return f"Moved {len(deleted)} mail(s) to Trash."


@mcp.tool()
def search_mails(query: str, folder: str | None = None) -> str:
    """Search mails by text in sender, subject or body. Optionally limit to one folder."""
    hits = backend.search_mails(query, folder)
    if not hits:
        return f"No mails matching '{query}'."
    return "\n".join(f"[{m.uid}] ({m.folder}) {m.sender} -- {m.subject}" for m in hits)


@mcp.tool()
def save_draft(body: str, reply_to_id: str | None = None, to: str | None = None, subject: str | None = None) -> str:
    """Save a draft mail (never sends). Either reply to an existing mail by id,
    or provide 'to' and 'subject' for a fresh draft."""
    reply_to = backend.get_mail(reply_to_id) if reply_to_id else None
    if reply_to_id and reply_to is None:
        return f"No mail with id {reply_to_id}."
    uid = backend.save_draft(body, reply_to=reply_to, subject=subject, to=to)
    return f"Draft saved (id {uid})."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
