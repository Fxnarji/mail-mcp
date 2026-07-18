# mail-mcp

IMAP mailbox MCP server, built for [hermes-agent](https://hermes-agent.nousresearch.com/) but usable by any MCP client. IMAP only — mail is never sent; replies are saved as drafts.

> **Status: frontend spike.** All tool surfaces are live against an in-memory fake mailbox. The real IMAP backend plugs in behind `backend.Backend` later.

## Surfaces

One server, three interaction styles. Pick per hermes entry via `tools.include`:

| Surface | Tools | Who decides | Best for |
|---|---|---|---|
| A — auto sort | `sort_inbox` | sampling model (server-driven) | hermes, low-tier models: one call sorts everything |
| B — spoon-fed | `next_mail`, `sort_mail` | the agent, one mail per call | any MCP client, no sampling needed |
| C — mailbox | `list_folders`, `list_mails`, `read_mail`, `move_mails`, `flag_mails`, `delete_mails`, `search_mails`, `save_draft` | the agent | interactive mailbox work |

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
    tools:
      include: [list_folders, list_mails, read_mail, move_mails,
                flag_mails, delete_mails, search_mails, save_draft]
```

For Surface B instead of A, use `include: [next_mail, sort_mail]`. Reload with `/reload-mcp` in hermes.

## Local development

```sh
uv run python tests/spike_client.py   # drives every surface, incl. a fake sampling model
uv run mail-mcp                       # run the server on stdio directly
```
