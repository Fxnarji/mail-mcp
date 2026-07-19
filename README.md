# mail-mcp

IMAP mailbox MCP server, built for [hermes-agent](https://hermes-agent.nousresearch.com/) but usable by any MCP client. IMAP only — mail is never sent; replies are saved as drafts.

> **Status: spike.** The server starts on an in-memory fake mailbox (safe to test against, resets on restart). A real IMAP backend is included: activate it with the `login` tool or `MAILMCP_*` env vars (below).

## Surfaces

One server, three interaction styles. Pick per hermes entry via `tools.include`:

| Surface | Tools | Who decides | Best for |
|---|---|---|---|
| A — auto sort | `sort_inbox` | sampling model (server-driven) | hermes, low-tier models: one call sorts everything |
| B — spoon-fed | `next_mail`, `sort_mail` | the agent, one mail per call | any MCP client, no sampling needed |
| C — mailbox | `list_folders`, `list_mails`, `read_mail`, `move_mails`, `flag_mails`, `delete_mails`, `search_mails`, `save_draft` | the agent | interactive mailbox work |

Cross-cutting: `login(host, user, password, port=993)` switches from the fake mailbox to a real IMAP account at runtime — useful when you can only reach the agent through chat. For a permanent account, set env vars instead: `MAILMCP_HOST`, `MAILMCP_USER`, `MAILMCP_PASSWORD` (optional `MAILMCP_PORT`) — with those set the server starts directly on the real account.

Folder names are matched case-insensitively and loosely (`newsletter` → `Newsletters`); unmatched names create a new folder. `sort_mail` returns the next mail automatically, so sorting is one tool call per mail.

## Install into hermes

Requires `uv` on the machine running hermes. In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  mail_sort:                       # Surface A: autonomous sorting
    command: "uvx"
    args: ["--from", "git+https://github.com/Fxnarji/mail-MCP", "mail-mcp"]
    tools:
      include: [sort_inbox]

  mail:                            # Surface C: interactive mailbox
    command: "uvx"
    args: ["--from", "git+https://github.com/Fxnarji/mail-MCP", "mail-mcp"]
    env:
      MAILMCP_HOST: "imap.example.com"   # omit the env block to start on the fake mailbox
      MAILMCP_USER: "you@example.com"
      MAILMCP_PASSWORD: "app-password"
    tools:
      include: [login, list_folders, list_mails, read_mail, move_mails,
                flag_mails, delete_mails, search_mails, save_draft]
```

For Surface B instead of A, use `include: [next_mail, sort_mail]`. Reload with `/reload-mcp` in hermes.

## Local development

```sh
uv run python tests/spike_client.py   # drives every surface, incl. a fake sampling model
uv run python tests/imap_smoke.py     # real-IMAP smoke test (needs MAILMCP_* env; --write for mutations)
uv run mail-mcp                       # run the server on stdio directly
```
