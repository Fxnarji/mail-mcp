"""mail-mcp server: three agent-facing surfaces over one Backend.

Surface A (hermes default): sort_inbox        -- sampling-driven, one call total
Surface B (portable Mode 1): next_mail/sort_mail -- one call per mail
Surface C (Mode 2):          general mailbox tools

Which surface an agent sees is decided by the client config (hermes
`tools.include`), not by the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent

from .backend import Backend, FakeBackend, Mail
from .backend_imap import IMAPBackend

mcp = FastMCP(
    "mail",
    host=os.environ.get("MAILMCP_BIND_HOST", "0.0.0.0"),
    port=int(os.environ.get("MAILMCP_BIND_PORT", "8000")),
)

logger = logging.getLogger("mail_mcp")


def _setup_logging() -> None:
    """Console (stderr -- stdout is the stdio transport's JSON-RPC channel)
    and/or file logging of sort decisions, configured via env vars:
    MAILMCP_LOG_CONSOLE (default on), MAILMCP_LOG_FILE (path, off by default)."""
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if os.environ.get("MAILMCP_LOG_CONSOLE", "1").lower() not in ("0", "false", "no"):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    log_file = os.environ.get("MAILMCP_LOG_FILE")
    if log_file:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(fmt)
        logger.addHandler(handler)


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
# Account access
# --------------------------------------------------------------------------

@mcp.tool()
def login(host: str, user: str, password: str, port: int = 993) -> str:
    """Log into a real IMAP mail account (TLS). Until this is called, all mail
    tools operate on a built-in TEST mailbox with fake mails."""
    global backend, _current_uid
    try:
        candidate = IMAPBackend(host=host, user=user, password=password, port=port)
        folders = candidate.list_folders()
    except Exception as exc:
        return f"Login failed: {exc}"
    backend = candidate
    _current_uid = None
    return (
        f"Logged in as {user} on {host}. {len(folders)} folders: {', '.join(folders)}.\n"
        "All mail tools now operate on this account."
    )


def _login_from_env() -> None:
    """Auto-login if credentials come via env (hermes config `env:` block)."""
    global backend
    host, user = os.environ.get("MAILMCP_HOST"), os.environ.get("MAILMCP_USER")
    password = os.environ.get("MAILMCP_PASSWORD")
    if host and user and password:
        backend = IMAPBackend(
            host=host, user=user, password=password,
            port=int(os.environ.get("MAILMCP_PORT", "993")),
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


def _apply_decision(mail: Mail, folder: str, response: str | None, model: str | None = None) -> str:
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
    logger.info(
        "sort mail=%r from=%r folder=%r model=%s reply=%r",
        mail.subject, mail.sender, resolved, model or "external", response,
    )
    return " ".join(parts)


# --------------------------------------------------------------------------
# Surface A: sampling-driven, server does everything
# --------------------------------------------------------------------------

_SORT_PROMPT = """You are sorting one email into a folder.
{policy}
{mail}

Existing folders: {folders}

Answer with ONLY a JSON object, no other text:
{{"folder": "<folder name, existing or new; INBOX to keep>", "reply": "<reply text, or null if no reply is needed>"}}

Only write a reply if the mail is personally addressed and clearly expects an answer."""


async def _sample_with_retry(
    ctx: Context, prompt: str, max_tokens: int = 400, retries: int = 5, backoff: float = 10.0
):
    """hermes caps sampling at a low requests/minute by default; a 25-mail
    inbox routinely exceeds it mid-run. Rate-limit and timeout errors are
    both transient (client-side throttling / a slow model response), so
    back off and retry instead of aborting the whole sort over one mail."""
    for attempt in range(retries + 1):
        try:
            return await ctx.session.create_message(
                messages=[SamplingMessage(role="user", content=TextContent(type="text", text=prompt))],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if attempt < retries and ("rate limit" in msg or "timed out" in msg or "timeout" in msg):
                logger.info("sample retry %d/%d after error: %s", attempt + 1, retries, exc)
                await asyncio.sleep(backoff)
                continue
            raise


def _default_sort_policy() -> str | None:
    """Server-configured fallback policy, read fresh on every call so it can
    be edited on the box without restarting the server. Takes effect only
    when the caller doesn't pass its own instructions."""
    path = os.environ.get("MAILMCP_SORT_POLICY_FILE")
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


@mcp.tool()
async def sort_inbox(ctx: Context, instructions: str | None = None) -> str:
    """Sort all new mail in the inbox into folders automatically and draft
    replies where needed. Call this once; it processes every new mail and
    returns a summary report.

    instructions: optional sorting policy -- your folder taxonomy, what
    counts as noise/phishing/personal/etc and where each should go, reply
    tone. Every per-mail decision follows it. If omitted, a server-configured
    default policy is used when one is set; otherwise folder and reply
    choices are the model's best generic guess."""
    report: list[str] = []
    limit = 25  # spike guard: never loop unbounded
    total = backend.count_unprocessed()
    done = 0
    if not instructions or not instructions.strip():
        instructions = _default_sort_policy()
    policy_block = f"\nSorting policy (follow this strictly):\n{instructions.strip()}\n" if instructions and instructions.strip() else ""
    logger.info("sort_inbox start total=%d limit=%d", total, limit)
    while limit > 0:
        limit -= 1
        mail = backend.next_unprocessed()
        if mail is None:
            break
        drafts = backend.drafts_folder()
        prompt = _SORT_PROMPT.format(
            policy=policy_block,
            mail=_format_mail(mail),
            folders=", ".join(f for f in backend.list_folders() if f != drafts),
        )
        try:
            result = await _sample_with_retry(ctx, prompt)
        except Exception as exc:
            report.append(f"STOPPED: sampling unavailable ({exc}). Use next_mail/sort_mail instead.")
            logger.info("sort_inbox stopped %d/%d: sampling unavailable (%s)", done, total, exc)
            break
        raw_reply = result.content.text if isinstance(result.content, TextContent) else ""
        logger.info("sample mail=%r model=%s response=%r", mail.subject, result.model, raw_reply)
        decision = _parse_decision(raw_reply)
        if decision is None:
            backend.mark_processed(mail.uid)  # skip rather than loop forever on it
            report.append(f"SKIPPED '{mail.subject}': model answer was not valid JSON.")
            done += 1
            logger.info("sort_inbox progress %d/%d (skipped, bad JSON)", done, total)
            continue
        folder, reply = decision
        report.append(_apply_decision(mail, folder, reply, model=result.model))
        done += 1
        logger.info("sort_inbox progress %d/%d", done, total)
    if not report:
        logger.info("sort_inbox done 0/%d: nothing to sort", total)
        return "Inbox clear. Nothing to sort."
    if limit == 0 and backend.next_unprocessed() is not None:
        report.append("NOT DONE: more unsorted mail remains -- call sort_inbox again to continue.")
        logger.info("sort_inbox hit limit at %d/%d", done, total)
    else:
        logger.info("sort_inbox done %d/%d", done, total)
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


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("ok")


class _BearerAuthMiddleware:
    """Rejects requests to the MCP endpoint without a matching bearer token.

    Only guards `protected_path` (the actual mail-mcp endpoint) -- everything
    else, notably /health, passes through unauthenticated so a reverse-proxy
    liveness check (e.g. Pangolin/Traefik probing the backend before routing
    real traffic to it) doesn't itself get rejected and mark the server down.
    """

    def __init__(self, app, token: str, protected_path: str) -> None:
        self.app = app
        self.token = token
        self.protected_path = protected_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != self.protected_path:
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"").decode()
        if auth != f"Bearer {self.token}":
            # A JSON-RPC-shaped body (not plain text) so an unauthenticated
            # preflight probe -- one that hasn't attached the configured auth
            # header yet -- still sees a valid-looking MCP content type
            # instead of failing fast on a bare "Unauthorized" string.
            from starlette.responses import JSONResponse
            response = JSONResponse(
                {"jsonrpc": "2.0", "id": "server-error",
                 "error": {"code": -32600, "message": "Unauthorized"}},
                status_code=401,
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


# Mirrors the surface split documented in README.md. Lets the server itself
# restrict which tools it registers (via MAILMCP_SURFACE) instead of relying
# on the client's tools.include config -- useful when only the server side
# is easy to redeploy/reconfigure (e.g. no access to edit the client config).
_SURFACE_TOOLS = {
    "A": {"sort_inbox"},
    "B": {"next_mail", "sort_mail"},
    "C": {"login", "list_folders", "list_mails", "read_mail", "move_mails",
          "flag_mails", "delete_mails", "search_mails", "save_draft"},
}


def _apply_surface_filter() -> None:
    surface = os.environ.get("MAILMCP_SURFACE")
    if not surface:
        return  # unfiltered: every tool registered, as before
    surface = surface.upper()
    if surface not in _SURFACE_TOOLS:
        sys.exit(f"MAILMCP_SURFACE must be one of {sorted(_SURFACE_TOOLS)}, got {surface!r}.")
    keep = _SURFACE_TOOLS[surface]
    for tool in asyncio.run(mcp.list_tools()):
        if tool.name not in keep:
            mcp.remove_tool(tool.name)


def main() -> None:
    _setup_logging()
    _apply_surface_filter()
    _login_from_env()
    transport = os.environ.get("MAILMCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
        return
    if transport != "http":
        sys.exit(f"MAILMCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}.")

    token = os.environ.get("MAILMCP_AUTH_TOKEN")
    if not token:
        sys.exit("MAILMCP_AUTH_TOKEN must be set to expose mail-mcp over MAILMCP_TRANSPORT=http.")

    import uvicorn

    app = mcp.streamable_http_app()
    app.add_middleware(_BearerAuthMiddleware, token=token, protected_path=mcp.settings.streamable_http_path)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)


if __name__ == "__main__":
    main()
