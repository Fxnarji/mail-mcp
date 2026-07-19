"""Real IMAP implementation of the Backend protocol (stdlib imaplib).

Mail ids handed to the agent are short session-local handles ("1", "2", ...)
mapped internally to (folder, imap_uid) pairs -- IMAP UIDs are per-folder and
change on move, which is exactly the trap we don't want agents to deal with.

"Unprocessed" inbox mail = UNSEEN and missing our custom keyword. Sorting a
mail into a folder moves it (flags travel along); keeping it in INBOX marks
it \\Seen + MailMCPProcessed so it is never fed to the agent again.

Dead connections (dropped by the server after idle) are retried once via
_reconnecting. Known spike limitation: no modified-UTF7 folder names.
"""

from __future__ import annotations

import email
import email.message
import email.utils
import functools
import imaplib
import re
import threading
import time
from email.header import decode_header, make_header
from email.mime.text import MIMEText

from .backend import Mail

PROCESSED_KEYWORD = "MailMCPProcessed"

_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')

_DRAFTS_NAMES = ("drafts", "draft", "inbox.drafts")
_TRASH_NAMES = ("trash", "deleted items", "deleted", "junk", "inbox.trash")


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decode_hdr(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return _decode_payload(part)
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get_filename():
                return _decode_payload(part)
        return "(no text body)"
    return _decode_payload(msg)


def _decode_payload(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return str(part.get_payload())
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _reconnecting(fn):
    """Retry a public backend method once on a dead connection.

    IMAP servers drop idle connections; the stdio server can outlive them by
    a long stretch of non-mail conversation. UIDs stay valid across a
    re-login (same UIDVALIDITY), so handles survive.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            try:
                return fn(self, *args, **kwargs)
            except (imaplib.IMAP4.abort, OSError):
                self._reconnect()
                return fn(self, *args, **kwargs)

    return wrapper


class IMAPBackend:
    def __init__(self, host: str, user: str, password: str, port: int = 993) -> None:
        self.host, self.port, self.user = host, port, user
        self._password = password
        self._lock = threading.RLock()
        self._connect()
        # short handle -> (folder, uid); agents never see raw IMAP UIDs
        self._handles: dict[str, tuple[str, str]] = {}
        self._rev: dict[tuple[str, str], str] = {}
        self._next_handle = 1
        self.list_folders()  # populates delimiter, verifies the connection

    # --- plumbing ------------------------------------------------------

    def _connect(self) -> None:
        self.conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
        self.conn.login(self.user, self._password)
        self._delim = "/"
        self._selected: str | None = None

    def _reconnect(self) -> None:
        try:
            self.conn.shutdown()
        except OSError:
            pass
        self._connect()

    def _handle_for(self, folder: str, uid: str) -> str:
        key = (folder, uid)
        if key not in self._rev:
            handle = str(self._next_handle)
            self._next_handle += 1
            self._rev[key] = handle
            self._handles[handle] = key
        return self._rev[key]

    def _forget(self, handle: str) -> None:
        key = self._handles.pop(handle, None)
        if key:
            self._rev.pop(key, None)

    def _select(self, folder: str) -> None:
        if self._selected != folder:
            typ, _ = self.conn.select(_quote(folder))
            if typ != "OK":
                raise RuntimeError(f"cannot open folder {folder!r}")
            self._selected = folder

    def _uid(self, *args: str):
        typ, data = self.conn.uid(*args)
        if typ != "OK":
            raise RuntimeError(f"IMAP {args[0]} failed: {data}")
        return data

    def _fetch_mail(self, folder: str, uid: str, headers_only: bool = False) -> Mail | None:
        self._select(folder)
        section = "BODY.PEEK[HEADER]" if headers_only else "BODY.PEEK[]"
        data = self._uid("FETCH", uid, f"({section} FLAGS)")
        raw = next((part[1] for part in data if isinstance(part, tuple)), None)
        if raw is None:
            return None
        msg = email.message_from_bytes(raw)
        # FLAGS live in the fetch metadata: tuple headers + trailing bytes parts
        meta = b" ".join([p[0] for p in data if isinstance(p, tuple)] + [p for p in data if isinstance(p, bytes)])
        flags_match = re.search(rb"FLAGS \(([^)]*)\)", meta)
        flags_text = flags_match.group(1).decode("ascii", "ignore") if flags_match else ""
        flags = {f.lstrip("\\") for f in flags_text.split() if f}
        return Mail(
            uid=self._handle_for(folder, uid),
            folder=folder,
            sender=_decode_hdr(msg.get("From")),
            subject=_decode_hdr(msg.get("Subject")),
            date=msg.get("Date", ""),
            body="" if headers_only else _text_body(msg),
            flags=flags,
            processed=PROCESSED_KEYWORD in flags,
            message_id=msg.get("Message-ID", ""),
        )

    def _special_folder(self, candidates: tuple[str, ...], fallback: str) -> str:
        for f in self.list_folders():
            if f.casefold() in candidates or f.casefold().split(self._delim)[-1] in candidates:
                return f
        resolved, _ = self.resolve_folder(fallback)
        return resolved

    # --- reading -------------------------------------------------------

    @_reconnecting
    def list_folders(self) -> list[str]:
        with self._lock:
            typ, data = self.conn.list()
            if typ != "OK":
                raise RuntimeError("LIST failed")
            folders = []
            for line in data:
                if not isinstance(line, bytes):
                    continue
                m = _LIST_RE.match(line)
                if not m:
                    continue
                if rb"\Noselect" in m.group("flags"):
                    continue
                delim = m.group("delim")
                if delim != b"NIL":
                    self._delim = delim[1:-1].decode()
                name = m.group("name").strip()
                if name.startswith(b'"') and name.endswith(b'"'):
                    name = name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
                folders.append(name.decode("utf-8", errors="replace"))
            # stable order, INBOX first
            folders.sort(key=lambda f: (f.upper() != "INBOX", f.casefold()))
            self._folders_cache = folders
            return folders

    @_reconnecting
    def list_mails(self, folder: str) -> list[Mail]:
        with self._lock:
            resolved, _ = self.resolve_folder(folder)
            self._select(resolved)
            data = self._uid("SEARCH", "ALL")
            uids = data[0].split() if data and data[0] else []
            mails = []
            for uid in uids[-50:]:  # newest 50; enough for the spike
                m = self._fetch_mail(resolved, uid.decode(), headers_only=True)
                if m:
                    mails.append(m)
            return mails

    @_reconnecting
    def get_mail(self, uid: str) -> Mail | None:
        with self._lock:
            key = self._handles.get(uid)
            if key is None:
                return None
            return self._fetch_mail(key[0], key[1])

    @_reconnecting
    def next_unprocessed(self) -> Mail | None:
        with self._lock:
            self._select("INBOX")
            data = self._uid("SEARCH", "UNSEEN", "UNKEYWORD", PROCESSED_KEYWORD)
            uids = data[0].split() if data and data[0] else []
            if not uids:
                return None
            return self._fetch_mail("INBOX", uids[0].decode())

    @_reconnecting
    def mark_processed(self, uid: str) -> None:
        with self._lock:
            key = self._handles.get(uid)
            if key is None:
                return
            folder, imap_uid = key
            self._select(folder)
            # \Seen carries the semantics even if the server rejects keywords
            try:
                self._uid("STORE", imap_uid, "+FLAGS", f"(\\Seen {PROCESSED_KEYWORD})")
            except RuntimeError:
                self._uid("STORE", imap_uid, "+FLAGS", "(\\Seen)")

    # --- writing -------------------------------------------------------

    @_reconnecting
    def resolve_folder(self, name: str) -> tuple[str, bool]:
        with self._lock:
            folders = getattr(self, "_folders_cache", None) or self.list_folders()
            want = name.strip().casefold()
            for f in folders:
                if f.casefold() == want:
                    return f, False
            for f in folders:  # loose: leaf name, prefix, singular/plural
                leaf = f.casefold().split(self._delim)[-1]
                if leaf == want or leaf.startswith(want) or want.startswith(leaf):
                    return f, False
            clean = name.strip()
            typ, data = self.conn.create(_quote(clean))
            if typ != "OK":  # some servers require a namespace prefix
                clean = f"INBOX{self._delim}{name.strip()}"
                typ, data = self.conn.create(_quote(clean))
                if typ != "OK":
                    raise RuntimeError(f"cannot create folder {name!r}: {data}")
            try:
                self.conn.subscribe(_quote(clean))
            except Exception:
                pass
            self.list_folders()
            return clean, True

    @_reconnecting
    def move_mails(self, uids: list[str], folder: str) -> list[str]:
        with self._lock:
            resolved, _ = self.resolve_folder(folder)
            has_move = "MOVE" in self.conn.capabilities
            moved = []
            for handle in uids:
                key = self._handles.get(handle)
                if key is None:
                    continue
                src, imap_uid = key
                if src == resolved:
                    moved.append(handle)
                    continue
                self._select(src)
                if has_move:
                    self._uid("MOVE", imap_uid, _quote(resolved))
                else:
                    self._uid("COPY", imap_uid, _quote(resolved))
                    self._uid("STORE", imap_uid, "+FLAGS", "(\\Deleted)")
                    self.conn.expunge()
                self._forget(handle)  # uid in the target folder is new
                moved.append(handle)
            return moved

    @_reconnecting
    def set_flag(self, uids: list[str], flag: str, value: bool) -> list[str]:
        with self._lock:
            std = {"flagged": "\\Flagged", "seen": "\\Seen", "draft": "\\Draft", "answered": "\\Answered"}
            imap_flag = std.get(flag.casefold(), flag)
            changed = []
            for handle in uids:
                key = self._handles.get(handle)
                if key is None:
                    continue
                folder, imap_uid = key
                self._select(folder)
                self._uid("STORE", imap_uid, "+FLAGS" if value else "-FLAGS", f"({imap_flag})")
                changed.append(handle)
            return changed

    def delete_mails(self, uids: list[str]) -> list[str]:
        return self.move_mails(uids, self._special_folder(_TRASH_NAMES, "Trash"))

    @_reconnecting
    def search_mails(self, query: str, folder: str | None = None) -> list[Mail]:
        with self._lock:
            safe = query.replace("\\", " ").replace('"', " ").strip()
            folders = [self.resolve_folder(folder)[0]] if folder else self.list_folders()
            hits: list[Mail] = []
            for f in folders:
                for m in self._search_folder(f, safe):
                    hits.append(m)
                    if len(hits) >= 50:
                        return hits
            return hits

    def _search_folder(self, folder: str, safe: str) -> list[Mail]:
        try:
            self._select(folder)
        except RuntimeError:
            return []
        # full-text first; servers without an FTS index (e.g. smtp.dev) reject
        # TEXT/BODY, so degrade to sender+subject, then to client-side matching
        for crit in (("TEXT", _quote(safe)), ("OR", "FROM", _quote(safe), "SUBJECT", _quote(safe))):
            try:
                data = self._uid("SEARCH", *crit)
            except RuntimeError:
                continue
            uids = data[0].split() if data and data[0] else []
            found = [self._fetch_mail(folder, uid.decode(), headers_only=True) for uid in uids[:50]]
            return [m for m in found if m]
        want = safe.casefold()
        return [m for m in self.list_mails(folder) if want in m.subject.casefold() or want in m.sender.casefold()]

    @_reconnecting
    def save_draft(self, body: str, reply_to: Mail | None = None, subject: str | None = None, to: str | None = None) -> str:
        with self._lock:
            msg = MIMEText(body, "plain", "utf-8")
            if reply_to is not None:
                to = email.utils.parseaddr(reply_to.sender)[1] or reply_to.sender
                subject = reply_to.subject if reply_to.subject.lower().startswith("re:") else f"Re: {reply_to.subject}"
                if reply_to.message_id:
                    msg["In-Reply-To"] = reply_to.message_id
                    msg["References"] = reply_to.message_id
            msg["From"] = self.user
            msg["To"] = to or ""
            msg["Subject"] = subject or "(no subject)"
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg["Message-ID"] = email.utils.make_msgid()
            drafts = self._special_folder(_DRAFTS_NAMES, "Drafts")
            typ, data = self.conn.append(
                _quote(drafts), "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
            )
            if typ != "OK":
                raise RuntimeError(f"cannot save draft: {data}")
            return f"draft in {drafts}"
