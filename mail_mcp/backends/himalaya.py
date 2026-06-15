"""Himalaya backend: shells out to the himalaya CLI (v2.x)."""
from __future__ import annotations

import os

from . import Message, _guard_dest


class HimalayaBackend:
    """Demo-only backend that shells out to the himalaya CLI (v2.x).

    Purpose: drive a REAL OAuth mailbox (e.g. Outlook via himalaya + ortie) so
    moves and drafts show up live in outlook.live.com / the Outlook client for
    a demo -- something password-IMAP can't do anymore. Not pretty, but works
    today.

    Auth/transport live entirely in himalaya's own config (including the ortie
    token command); this class only invokes the CLI and maps its JSON onto
    Message. Like the other backends it exposes NO delete and NO send, and
    move refuses trash-like destinations.

    Message ids are encoded "FOLDER:HIMALAYA_ID" because himalaya ids are
    relative to a folder; we split the folder back out for read/move.

    Env vars:
        HIMALAYA_BIN            path to the binary        (default: "himalaya")
        HIMALAYA_ACCOUNT        --account name            (default: himalaya's default)
        HIMALAYA_DRAFTS_FOLDER  folder/alias for drafts   (default: "Drafts")
        HIMALAYA_TIMEOUT        per-call timeout seconds  (default: 60)
    """

    def __init__(self) -> None:
        self.bin = os.environ.get("HIMALAYA_BIN", "himalaya")
        self.account = os.environ.get("HIMALAYA_ACCOUNT") or None
        self.drafts_folder = os.environ.get("HIMALAYA_DRAFTS_FOLDER", "Drafts")
        self.timeout = int(os.environ.get("HIMALAYA_TIMEOUT", "60"))

    # -- subprocess -------------------------------------------------------
    def _run(self, args, *, want_json: bool = True, stdin: str | None = None):
        import subprocess
        import json
        cmd = [self.bin]
        if self.account:
            cmd += ["--account", self.account]
        if want_json:
            cmd += ["--json"]  # himalaya v2 global flag (v1's `--output json` is gone)
        cmd += list(args)
        proc = subprocess.run(cmd, input=stdin, capture_output=True,
                              text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"`himalaya {' '.join(args)}` failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        if not want_json:
            return proc.stdout
        out = (proc.stdout or "").strip()
        return json.loads(out) if out else None

    @staticmethod
    def _split_id(message_id: str):
        folder, _, hid = message_id.rpartition(":")
        if not folder or not hid:
            raise ValueError(f"Malformed id {message_id!r} (expected 'FOLDER:ID')")
        return folder, hid

    # -- JSON mapping (pure, unit-tested) ---------------------------------
    @staticmethod
    def _hname(name) -> str:
        if isinstance(name, dict):
            return str(name.get("other", "")).lower()
        return str(name or "").lower()

    @staticmethod
    def _val_text(v) -> str:
        return v.get("Text", "") if isinstance(v, dict) else ""

    @staticmethod
    def _val_addr_name(v) -> str:
        """from/to value -> display name (or address if unnamed)."""
        if not isinstance(v, dict):
            return ""
        addr = v.get("Address")
        if isinstance(addr, dict) and isinstance(addr.get("List"), list) and addr["List"]:
            e = addr["List"][0]
            return e.get("name") or e.get("address") or ""
        return v.get("Text", "")

    @staticmethod
    def _val_date(v) -> str:
        if not isinstance(v, dict):
            return ""
        dt = v.get("DateTime")
        if not isinstance(dt, dict):
            return v.get("Text", "")
        try:
            from datetime import datetime
            base = datetime(dt["year"], dt["month"], dt["day"],
                            dt.get("hour", 0), dt.get("minute", 0), dt.get("second", 0))
            sign = "-" if dt.get("tz_before_gmt") else "+"
            return f"{base.isoformat()}{sign}{dt.get('tz_hour', 0):02d}:{dt.get('tz_minute', 0):02d}"
        except Exception:
            return v.get("Text", "")

    @staticmethod
    def _part_text(part) -> str:
        b = part.get("body") if isinstance(part, dict) else None
        if isinstance(b, dict):
            for k in ("Text", "Html", "Plain"):
                if isinstance(b.get(k), str):
                    return b[k]
            for x in b.values():
                if isinstance(x, str):
                    return x
        return b if isinstance(b, str) else ""

    @classmethod
    def _select_body(cls, parsed) -> str:
        parts = parsed.get("parts", []) or []
        for key in ("text_body", "html_body"):
            chunks = [cls._part_text(parts[i]) for i in (parsed.get(key) or [])
                      if isinstance(i, int) and 0 <= i < len(parts)]
            chunks = [c for c in chunks if c]
            if chunks:
                return "\n".join(chunks)
        for p in parts:
            if (t := cls._part_text(p)):
                return t
        return ""

    @classmethod
    def _read_to_message(cls, data, message_id: str, folder: str) -> Message:
        if isinstance(data, list):
            data = data[0] if data else {}
        headers: dict = {}
        for part in (data.get("parts") or []):
            for h in (part.get("headers") or []):
                n = cls._hname(h.get("name"))
                if n and n not in headers:
                    headers[n] = h.get("value", {})
        body = cls._select_body(data)
        return Message(
            id=message_id, folder=folder,
            sender=cls._val_addr_name(headers.get("from")),
            subject=cls._val_text(headers.get("subject")),
            date=cls._val_date(headers.get("date")),
            unread=False, snippet=body[:120], body=body,
        )

    @classmethod
    def _envelope_to_message(cls, env, folder: str) -> Message:
        # himalaya v2 flags are objects: {"raw": "\\Seen", "iana": "seen"}
        flags = env.get("flags") or []

        def _is_seen(f):
            if isinstance(f, dict):
                f = f.get("iana") or f.get("raw") or ""
            return str(f).lower().lstrip("\\") == "seen"

        seen = any(_is_seen(f) for f in flags) if isinstance(flags, list) else False
        # himalaya v2 `from` is a list of {"name", "email"} objects
        frm = env.get("from")
        sender = ""
        if isinstance(frm, list) and frm:
            e0 = frm[0]
            sender = ((e0.get("name") or e0.get("email") or e0.get("address") or "")
                      if isinstance(e0, dict) else str(e0))
        elif isinstance(frm, dict):
            sender = (frm.get("name") or frm.get("email")
                      or frm.get("addr") or frm.get("address") or "")
        return Message(
            id=f"{folder}:{env.get('id')}", folder=folder, sender=sender,
            subject=env.get("subject") or "", date=str(env.get("date") or ""),
            unread=not seen, snippet="", body="",
        )

    # -- MailBackend interface --------------------------------------------
    def list_folders(self) -> list[str]:
        data = self._run(["mailbox", "list"]) or []
        if isinstance(data, dict):                 # v2 wraps the array
            data = data.get("mailboxes") or data.get("folders") or data.get("data") or []
        names = []
        for f in data:
            n = (f.get("name") or f.get("folder") or "") if isinstance(f, dict) else str(f)
            if n:
                names.append(n)
        return names

    def list_messages(self, folder: str = "INBOX", limit: int = 20) -> list[Message]:
        data = self._run(["envelope", "list", "-m", folder,
                          "--page-size", str(limit)]) or []
        if isinstance(data, dict):
            data = data.get("envelopes") or data.get("data") or []
        return [self._envelope_to_message(e, folder) for e in data if isinstance(e, dict)]

    def get_message(self, message_id: str) -> Message:
        folder, hid = self._split_id(message_id)
        data = self._run(["message", "read", hid, "-m", folder])
        return self._read_to_message(data, message_id, folder)

    def _ensure_mailbox_exists(self, folder: str) -> None:
        known = {name.lower() for name in self.list_folders()}
        if folder.lower() not in known:
            self._run(["imap", "mailbox", "create", folder], want_json=False)

    def move_message(self, message_id: str, dest_folder: str) -> dict:
        _guard_dest(dest_folder)
        folder, hid = self._split_id(message_id)
        self._ensure_mailbox_exists(dest_folder)
        # himalaya v2: `message move --from <src> --to <dest> <ID>...`
        self._run(["message", "move", "--from", folder, "--to", dest_folder, hid],
                  want_json=False)
        return {"status": "moved", "id": message_id, "from": folder, "to": dest_folder}

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        import email.message
        msg = email.message.EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        # himalaya 2.x: `message add -m <folder> --flag draft` reads the raw
        # message from stdin and appends it as a draft. No SMTP send occurs.
        self._run(["message", "add", "-m", self.drafts_folder, "--flag", "draft"],
                  want_json=False, stdin=msg.as_string())
        return {"status": "draft_created", "folder": self.drafts_folder,
                "to": to, "subject": subject}
    
    # himalaya v2 accepts: seen, answered, flagged, draft. "flagged" is the
    # standard \Flagged attention marker used by flag_message/unflag_message.
    _ALLOWED_FLAGS = {"flagged"}
 
    def _set_flag(self, message_id: str, flag: str, *, add: bool) -> dict:
        flag = flag.strip().lower().lstrip("\\")
        if flag not in self._ALLOWED_FLAGS:
            raise ValueError(
                f"Flag {flag!r} not allowed (permitted: {sorted(self._ALLOWED_FLAGS)}). "
                "Setting \\Deleted is blocked by design."
            )
        folder, hid = self._split_id(message_id)
        verb = "add" if add else "remove"
        # himalaya 2.x: `flag add|remove <id> --flag <name>`. We pass --folder
        # because ids are folder-relative (same as read/move). If your build
        # rejects --folder here, check `himalaya flag add --help` for placement.
        self._run(["flag", verb, hid, "--flag", flag, "-m", folder],
                  want_json=False)
        return {"status": f"flag_{verb}", "id": message_id, "flag": flag}
 
    def flag_message(self, message_id: str) -> dict:
        """Mark a message as needing attention (sets the \\Flagged flag)."""
        return self._set_flag(message_id, "flagged", add=True)
 
    def unflag_message(self, message_id: str) -> dict:
        """Clear the attention flag from a message (removes \\Flagged)."""
        return self._set_flag(message_id, "flagged", add=False)

    def create_summary(self, body: str) -> dict:
        import email.message
        msg = email.message.EmailMessage()
        msg["To"] = "Bob"
        msg["Subject"] = "Zusammenfassung"
        msg.set_content(body)
        # himalaya 2.x: `message add -m <folder> --flag draft` reads the raw
        # message from stdin and appends it as a draft. No SMTP send occurs.
        self._run(["message", "add", "-m", "Zusammenfassung", "--flag", "draft"],
                  want_json=False, stdin=msg.as_string())
        return {"status": "summary created", "folder": "Zusammenfassung",
                "to": "Bob", "subject": "Zusammenfassung"}
