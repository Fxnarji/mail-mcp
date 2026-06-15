"""Parse himalaya's `message read -o json` output into a Message.

himalaya emits a parsed-MIME structure: a list of `parts`, each with `headers`
and a `body`, plus top-level `text_body` / `html_body` index lists pointing at
which parts make up the displayable body, and an `attachments` list.

Header values are a tagged union keyed by variant name:
    {"Text": "..."}                                  plain string
    {"Address": {"List": [{"name", "address"}, ...]}}  address list (from/to)
    {"DateTime": {year, month, ...}}                   structured date
    {"ContentType": {...}}                             content type
A header `name` is usually a string ("subject") but may be {"other": "X-..."}
for non-standard headers. The helpers below normalise all of that.
"""
from __future__ import annotations

import json
from datetime import datetime

from .backends import EmailAttachment, Message


# --------------------------------------------------------------------------- helpers

def _header_name(name) -> str:
    """Normalise a header name to lowercase; handle the {'other': '...'} form."""
    if isinstance(name, dict):
        return str(name.get("other", "")).lower()
    return str(name or "").lower()


def _addresses(value) -> list[tuple[str, str]]:
    """Extract (display_name, address) pairs from an Address header value."""
    if not isinstance(value, dict):
        return []
    addr = value.get("Address")
    entries: list = []
    if isinstance(addr, dict):
        if isinstance(addr.get("List"), list):
            entries = addr["List"]
        elif "address" in addr:                 # single-address shape
            entries = [addr]
        elif isinstance(addr.get("Single"), dict):
            entries = [addr["Single"]]
    return [(e.get("name") or "", e.get("address") or "")
            for e in entries if isinstance(e, dict)]


def _format_datetime(value) -> str:
    """Render a DateTime header value as an ISO-8601 string, else fall back to Text."""
    if not isinstance(value, dict):
        return ""
    dt = value.get("DateTime")
    if not isinstance(dt, dict):
        return value.get("Text", "")
    try:
        base = datetime(dt["year"], dt["month"], dt["day"],
                        dt.get("hour", 0), dt.get("minute", 0), dt.get("second", 0))
        sign = "-" if dt.get("tz_before_gmt") else "+"
        return f"{base.isoformat()}{sign}{dt.get('tz_hour', 0):02d}:{dt.get('tz_minute', 0):02d}"
    except Exception:
        return value.get("Text", "")


def _part_body_text(part) -> str:
    body = part.get("body") if isinstance(part, dict) else None
    if isinstance(body, dict):
        for key in ("Text", "Html", "Plain"):
            if isinstance(body.get(key), str):
                return body[key]
        for v in body.values():                 # fall back to any string value
            if isinstance(v, str):
                return v
    elif isinstance(body, str):
        return body
    return ""


def _select_body(parsed) -> str:
    """Pick the displayable body: prefer text_body parts, then html_body, then any."""
    parts = parsed.get("parts", []) or []
    for key in ("text_body", "html_body"):
        chunks = [
            _part_body_text(parts[i])
            for i in (parsed.get(key) or [])
            if isinstance(i, int) and 0 <= i < len(parts)
        ]
        chunks = [c for c in chunks if c]
        if chunks:
            return "\n".join(chunks)
    for p in parts:
        if (t := _part_body_text(p)):
            return t
    return ""


def _parse_attachments(parsed) -> list[EmailAttachment]:
    """Build EmailAttachment entries. NOTE: confirm these keys against a real
    message that actually has attachments -- the sample's list was empty, so the
    key names below are defensive guesses with fallbacks."""
    out: list[EmailAttachment] = []
    for a in parsed.get("attachments", []) or []:
        if not isinstance(a, dict):
            continue
        out.append(EmailAttachment(
            filename=a.get("filename") or a.get("name") or "",
            content_type=a.get("content_type") or a.get("mime") or a.get("type") or "",
            size=int(a.get("size") or 0),
        ))
    return out


def _collect_headers(parsed) -> dict:
    """First-occurrence lookup of header name -> value, scanned across all parts.
    (Message-level headers like subject/from/to/date live on the first part for
    single-part mail; scanning all parts also covers simple multipart cases.)"""
    headers: dict = {}
    for part in parsed.get("parts", []) or []:
        for h in part.get("headers", []) or []:
            name = _header_name(h.get("name"))
            if name and name not in headers:
                headers[name] = h.get("value", {})
    return headers


# --------------------------------------------------------------------------- entry point

def parse_message(message_id: str, raw) -> Message:
    """Convert raw himalaya `message read` output (JSON str/bytes or dict) into a Message."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(parsed, list):                 # some commands wrap output in a list
        parsed = parsed[0] if parsed else {}

    headers = _collect_headers(parsed)

    subj_val = headers.get("subject")
    subject = subj_val.get("Text", "") if isinstance(subj_val, dict) else ""

    sender_name = sender_addr = ""
    if "from" in headers:
        addrs = _addresses(headers["from"])
        if addrs:
            sender_name, sender_addr = addrs[0]
        else:
            sender_name = headers["from"].get("Text", "") if isinstance(headers["from"], dict) else ""

    receiver = ""
    if "to" in headers:
        addrs = _addresses(headers["to"])
        receiver = (", ".join(a for _, a in addrs if a)
                    or ", ".join(n for n, _ in addrs if n))

    return Message(
        id=message_id,
        flags=[],  # `message read` carries no IMAP flags; get those from the envelope/list output
        subject=subject,
        sender=sender_name,
        sender_mail=sender_addr,
        receiver=receiver,
        date=_format_datetime(headers.get("date", {})),
        body=_select_body(parsed),
        attachments=_parse_attachments(parsed),
    )