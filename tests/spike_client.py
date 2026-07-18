"""Frontend spike harness: drives the MCP server exactly like a client would.

- launches the server through the packaged entry point (install check)
- exercises Surface B (next_mail/sort_mail), Surface A (sort_inbox via a fake
  sampling model), and Surface C (Mode 2 tools)

Run: uv run python tests/spike_client.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.context import RequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent

ROOT = Path(__file__).resolve().parent.parent


async def fake_model(
    context: RequestContext, params: CreateMessageRequestParams
) -> CreateMessageResult:
    """Stands in for the hermes sampling model: canned per-sender decisions."""
    prompt = params.messages[0].content.text  # type: ignore[union-attr]
    if "anna.k@" in prompt:
        decision = {"folder": "INBOX", "reply": "Hi Anna, Friday 7pm works great. See you there!"}
    elif "github.com" in prompt:
        decision = {"folder": "CI Alerts", "reply": None}  # new folder on purpose
    elif "linkedin.com" in prompt:
        decision = {"folder": "Junk", "reply": None}  # new folder on purpose
    else:
        decision = {"folder": "Archive", "reply": None}
    print(f"    [sampling request received -> answering {decision['folder']}]")
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=json.dumps(decision)),
        model="spike-fake-model",
        stopReason="endTurn",
    )


async def call(session: ClientSession, tool: str, args: dict | None = None) -> str:
    result = await session.call_tool(tool, args or {})
    text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    print(f"\n>>> {tool}({json.dumps(args) if args else ''})")
    print("\n".join("    " + line for line in text.splitlines()))
    return text


async def main() -> None:
    params = StdioServerParameters(
        command="uv", args=["run", "--project", str(ROOT), "mail-mcp"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, sampling_callback=fake_model) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("== Registered tools ==")
            print(", ".join(t.name for t in tools.tools))

            print("\n\n== Surface B: next_mail / sort_mail loop ==")
            await call(session, "sort_mail", {"folder": "Archive"})  # error path: nothing pending
            await call(session, "next_mail")
            await call(session, "sort_mail", {"folder": "invoices"})  # loose match -> Invoices
            await call(session, "sort_mail", {"folder": "newsletter"})  # loose match -> Newsletters

            print("\n\n== Surface A: sort_inbox (sampling-driven) ==")
            await call(session, "sort_inbox")
            await call(session, "sort_inbox")  # second run: inbox should be clear

            print("\n\n== Surface C: Mode 2 tools ==")
            await call(session, "list_folders")
            await call(session, "list_mails", {"folder": "Invoices"})
            await call(session, "search_mails", {"query": "friday"})
            await call(session, "list_mails", {"folder": "Drafts"})
            drafts = await call(session, "list_mails", {"folder": "CI Alerts"})
            uid = drafts.split("]")[0].strip("[")
            await call(session, "read_mail", {"mail_id": uid})
            await call(session, "flag_mails", {"mail_ids": [uid]})
            await call(session, "delete_mails", {"mail_ids": [uid]})
            await call(session, "list_mails", {"folder": "Trash"})

    print("\nSpike complete.")


if __name__ == "__main__":
    asyncio.run(main())
