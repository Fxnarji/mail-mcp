"""Smoke test for IMAPBackend against a real account (e.g. smtp.dev sandbox).

Credentials via env:
  MAILMCP_HOST, MAILMCP_USER, MAILMCP_PASSWORD, [MAILMCP_PORT=993]

Run: uv run python tests/imap_smoke.py

Read-mostly: it lists folders/mails and peeks at the next unprocessed mail.
Pass --write to also exercise folder creation, move, flags and draft saving
(touches real data in the account: creates 'MailMCP Test' folder, moves the
newest inbox mail there and back, saves one draft).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mail_mcp.backend_imap import IMAPBackend  # noqa: E402


def main() -> None:
    host, user, password = (os.environ.get(k) for k in ("MAILMCP_HOST", "MAILMCP_USER", "MAILMCP_PASSWORD"))
    if not (host and user and password):
        sys.exit("Set MAILMCP_HOST, MAILMCP_USER, MAILMCP_PASSWORD first.")
    backend = IMAPBackend(host=host, user=user, password=password, port=int(os.environ.get("MAILMCP_PORT", "993")))

    folders = backend.list_folders()
    print(f"Connected. Folders ({len(folders)}): {', '.join(folders)}")

    inbox = backend.list_mails("INBOX")
    print(f"\nINBOX: {len(inbox)} mail(s) (newest 50)")
    for m in inbox[-5:]:
        print(f"  [{m.uid}] {m.sender} -- {m.subject} (flags: {sorted(m.flags)})")

    nxt = backend.next_unprocessed()
    if nxt:
        print(f"\nNext unprocessed: [{nxt.uid}] {nxt.sender} -- {nxt.subject}")
        print(f"  body preview: {nxt.body[:150]!r}")
    else:
        print("\nNo unprocessed (unseen) mail in INBOX.")

    if "--write" not in sys.argv:
        print("\nRead-only smoke OK. Re-run with --write for the mutation tests.")
        return

    print("\n-- write tests --")
    resolved, created = backend.resolve_folder("MailMCP Test")
    print(f"resolve_folder('MailMCP Test') -> {resolved!r} (created={created})")

    if inbox:
        victim = inbox[-1]
        print(f"moving [{victim.uid}] '{victim.subject}' to {resolved}...")
        backend.move_mails([victim.uid], resolved)
        there = backend.list_mails(resolved)
        print(f"  {resolved} now has {len(there)} mail(s)")
        back = next((m for m in there if m.subject == victim.subject), None)
        if back:
            backend.set_flag([back.uid], "Flagged", True)
            print(f"  flagged [{back.uid}], moving back to INBOX")
            backend.move_mails([back.uid], "INBOX")

    where = backend.save_draft("This is a mail-mcp smoke test draft.", subject="mail-mcp smoke", to=user)
    print(f"save_draft -> {where}")

    hits = backend.search_mails("smoke")
    print(f"search 'smoke': {len(hits)} hit(s): " + "; ".join(f"({m.folder}) {m.subject}" for m in hits[:5]))
    print("\nWrite smoke OK.")


if __name__ == "__main__":
    main()
