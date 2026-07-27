"""Interactive client: drive the MCP server one call at a time and see
exactly the text a model would receive -- no LLM in the loop.

Surface B/C: type a tool call yourself, read back the raw tool result.
Surface A (sort_inbox): the server pauses on each mail and asks *you* to
answer the sampling prompt, i.e. you roleplay the model's JSON decision.

Usage:
    uv run python tests/roleplay_client.py                    # stdio, FakeBackend
    uv run python tests/roleplay_client.py <url> [token]      # e.g. http://host:8123/mcp

At the prompt: tool_name [key=value ...]   (values are parsed as JSON if
possible, so value=true, mail_ids=["1","2"], folder=Invoices all work)
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.context import RequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent


async def human_sampling_callback(
    context: RequestContext, params: CreateMessageRequestParams
) -> CreateMessageResult:
    prompt = params.messages[0].content.text  # type: ignore[union-attr]
    print("\n=== sampling request -- you are the model now ===")
    print(prompt)
    reply = input('=== paste your JSON reply, e.g. {"folder": "INBOX", "reply": null} ===\nmodel> ')
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=reply),
        model="human-roleplay",
        stopReason="endTurn",
    )


def _parse_args(tokens: list[str]) -> dict:
    args = {}
    for tok in tokens:
        if "=" not in tok:
            print(f"  (ignoring {tok!r}, expected key=value)")
            continue
        key, _, val = tok.partition("=")
        try:
            args[key] = json.loads(val)
        except json.JSONDecodeError:
            args[key] = val
    return args


async def _repl(session: ClientSession) -> None:
    tools = await session.list_tools()
    print("Available tools:", ", ".join(t.name for t in tools.tools))
    print("Type: tool_name [key=value ...]   (empty line or Ctrl-D to quit)\n")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        tokens = shlex.split(line)
        name, rest = tokens[0], tokens[1:]
        args = _parse_args(rest)
        try:
            result = await session.call_tool(name, args)
        except Exception as exc:
            print(f"ERROR: {exc}\n")
            continue
        text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        print(text, "\n")


async def main() -> None:
    if len(sys.argv) > 1:
        from mcp.client.streamable_http import streamablehttp_client

        url = sys.argv[1]
        token = sys.argv[2] if len(sys.argv) > 2 else None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write, sampling_callback=human_sampling_callback) as session:
                await session.initialize()
                await _repl(session)
    else:
        params = StdioServerParameters(command="uv", args=["run", "mail-mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, sampling_callback=human_sampling_callback) as session:
                await session.initialize()
                await _repl(session)


if __name__ == "__main__":
    asyncio.run(main())
