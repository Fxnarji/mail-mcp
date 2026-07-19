"""Generate a one-shot install+run prompt for the hermes agent and copy it
to the clipboard.

Usage:
    python generate_prompt.py A     # sort_inbox (sampling-driven)
    python generate_prompt.py B     # next_mail / sort_mail loop
    python generate_prompt.py C     # Mode 2 mailbox tools

Credentials and repo URL come from creds.py (gitignored).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from textwrap import dedent

try:
    import creds
except ImportError:
    sys.exit("creds.py not found next to this script (it is gitignored -- create it, see README).")

SURFACES = {
    "A": {
        "include": ["sort_inbox"],
        "tools": "sort_inbox",
        "task": (
            "Call sort_inbox once. It processes every new mail by itself (this can take a "
            "while -- it asks a model to decide each mail) and returns a report of what was "
            "sorted where and which draft replies were written. Relay the full report to me verbatim."
        ),
    },
    "B": {
        "include": ["next_mail", "sort_mail"],
        "tools": "next_mail, sort_mail",
        "task": (
            "Sort the inbox: call next_mail once to get the first mail. For each mail, decide a "
            "fitting folder and call sort_mail with it (any folder name works -- existing ones "
            "match loosely, unknown names create a new folder, 'INBOX' keeps the mail where it "
            "is). If a mail is personally addressed and clearly expects an answer, also pass "
            "response=\"...\" with a short reply; it is saved as a draft, never sent. Each "
            "sort_mail result already contains the NEXT mail, so keep calling sort_mail until it "
            "says 'Inbox clear', then summarize every decision you made."
        ),
    },
    "C": {
        "include": [
            "list_folders", "list_mails", "read_mail", "move_mails",
            "flag_mails", "delete_mails", "search_mails", "save_draft",
        ],
        "tools": (
            "list_folders, list_mails, read_mail, move_mails, flag_mails, "
            "delete_mails, search_mails, save_draft"
        ),
        "task": (
            "Organize the mailbox: list_folders and list_mails on INBOX first, read the "
            "interesting mails with read_mail, then file every inbox mail into a sensible folder "
            "with move_mails (create new folder names freely). Flag anything urgent with "
            "flag_mails. Finish with an overview of the folder structure you ended up with."
        ),
    },
}


def _yaml_entry(surface: str) -> str:
    include = ", ".join(SURFACES[surface]["include"])
    return dedent(f"""\
        mcp_servers:
          mail:
            command: "uvx"
            args: ["--from", "git+{creds.REPO_URL}", "mail-mcp"]
            env:
              MAILMCP_HOST: "{creds.MAILMCP_HOST}"
              MAILMCP_PORT: "{creds.MAILMCP_PORT}"
              MAILMCP_USER: "{creds.MAILMCP_USER}"
              MAILMCP_PASSWORD: "{creds.MAILMCP_PASSWORD}"
            tools:
              include: [{include}]""")


def generate_prompt(surface: str = "A") -> str:
    surface = surface.upper()
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {sorted(SURFACES)}, got {surface!r}")
    s = SURFACES[surface]
    return dedent("""\
        Install a mail MCP server into your own config, verify it, then sort my test mailbox. Work through the steps in order and tell me what you did at each step.

        Step 1 -- Backup: copy ~/.hermes/config.yaml to ~/.hermes/config.yaml.bak

        Step 2 -- Edit ~/.hermes/config.yaml. If a top-level `mcp_servers:` key already exists, add the `mail:` entry below under it (do NOT create a second mcp_servers key, do not touch other entries). If an entry named `mail` already exists, replace it entirely. The entry, exactly:

        {yaml}

        Step 3 -- Verify the file is still valid YAML before moving on (e.g. parse it with python). If you broke it, restore the backup and try again.

        Step 4 -- Wait for the MCP auto-reload to pick up the change, then confirm you now have these tools: {tools}. The first launch downloads the package, so allow up to 2 minutes. The server logs into the mail account by itself via the env vars. If the tools have not appeared after 2 minutes, report what you observe and stop.

        Step 5 -- {task}

        Context: this is a disposable TEST mailbox (fake company data), so acting on it is safe. These tools cannot send mail at all -- replies only ever become drafts.
    """).format(yaml=_indent(_yaml_entry(surface)), tools=s["tools"], task=s["task"])


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())


def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "win32":
            # via temp file + Set-Clipboard: clip.exe needs a UTF-16 BOM that
            # then ends up pasted as an invisible character
            fd, path = tempfile.mkstemp(suffix=".txt", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 '{path}')"],
                    check=True,
                )
            finally:
                os.unlink(path)
        elif sys.platform == "darwin":
            subprocess.run("pbcopy", input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def main() -> None:
    surface = next((a for a in sys.argv[1:] if not a.startswith("-")), "A")
    prompt = generate_prompt(surface)
    print(prompt)
    if copy_to_clipboard(prompt):
        print(f"--- Surface {surface.upper()} prompt copied to clipboard ({len(prompt)} chars). ---")
    else:
        print("--- Clipboard copy failed; use the text printed above. ---")


if __name__ == "__main__":
    main()
