"""Bounded source capture with public-IP-pinned HTTP and portable receipts."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _canonical(url):
    from decision_map import _canonical_url

    return _canonical_url(url)


def _public_addresses(host, port):
    addresses = sorted(
        {row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("non_public_address")
    return addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, *, timeout, secure):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure = address, secure

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            self.sock = ssl.create_default_context().wrap_socket(
                self.sock, server_hostname=self.host
            )


def fetch_public(url: str, *, seconds=8, max_bytes=256000) -> dict:
    """No auth, cookies, proxy inheritance, private IPs, or unvalidated redirects."""
    deadline = time.monotonic() + seconds
    original = _canonical(url)
    current = original
    if not original:
        raise ValueError("unsafe_url")
    for _ in range(4):
        parsed = urlsplit(current)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443) or parsed.username or parsed.password:
            raise ValueError("unsafe_authority")
        addresses = _public_addresses(parsed.hostname, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("capture_deadline")
        connection = _PinnedHTTP(
            parsed.hostname,
            port,
            addresses[0],
            timeout=remaining,
            secure=parsed.scheme == "https",
        )
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": "MoA-X evidence capture",
                    "Accept": "text/html,text/plain,application/json",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                current = _canonical(
                    urljoin(current, response.getheader("Location", ""))
                )
                if not current:
                    raise ValueError("unsafe_redirect")
                continue
            if response.status != 200:
                raise ValueError(f"http_{response.status}")
            content_type = response.getheader("Content-Type", "").split(";")[0].lower()
            if content_type not in (
                "text/html",
                "text/plain",
                "application/xhtml+xml",
                "application/json",
            ):
                raise ValueError("unsupported_content_type")
            raw = bytearray()
            while len(raw) <= max_bytes:
                if time.monotonic() >= deadline:
                    raise TimeoutError("capture_deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(max(0.1, deadline - time.monotonic()))
                chunk = response.read(min(16384, max_bytes + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            clipped = len(raw) > max_bytes
            raw = bytes(raw[:max_bytes])
            text = raw.decode("utf-8", errors="replace")
            if "html" in content_type:
                parser = _Text()
                parser.feed(text)
                text = " ".join(parser.parts)
            text = " ".join(text.split())
            if not text:
                raise ValueError("empty_source")
            return {
                "url": original,
                "final_url": current,
                "state": "captured",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "text": text[:64000],
                "truncated": clipped or len(text) > 64000,
            }
        finally:
            connection.close()
    raise ValueError("redirect_limit")


def _fetch_child(url, connection):
    try:
        connection.send(fetch_public(url))
    except Exception as error:
        connection.send(
            {
                "url": url,
                "state": "unavailable",
                "reason": type(error).__name__,
                "text": "",
            }
        )
    finally:
        connection.close()


def _bounded_fetch(url):
    # A process boundary also bounds blocking system DNS resolution. Killing a
    # stuck resolver must not strand the worker or the ThreadPool's shutdown.
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_fetch_child, args=(url, writer), daemon=True)
    try:
        process.start()
        writer.close()
        if not reader.poll(10):
            raise TimeoutError("capture_deadline")
        return reader.recv()
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()


def load(session: Path) -> dict:
    path = session / "source-receipts.json"
    if path.is_symlink():
        return {}
    try:
        if path.stat().st_size > 3_000_000:
            return {}
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def capture(session: Path, urls: list[str], *, limit=12, fetcher=None) -> dict:
    from run_moa import _write_text_atomic

    cached = load(session)
    pending = sorted(
        {value for url in urls if (value := _canonical(url)) and value not in cached}
    )
    remaining = max(0, limit - len(cached))

    def one(url):
        try:
            return url, (fetcher or _bounded_fetch)(url)
        except Exception as error:
            # No response bodies, credentials, or account details in diagnostics.
            return url, {
                "url": url,
                "state": "unavailable",
                "reason": type(error).__name__,
                "text": "",
            }

    with ThreadPoolExecutor(max_workers=4) as pool:
        for url, receipt in pool.map(one, pending[:remaining]):
            cached[url] = receipt
    _write_text_atomic(
        session / "source-receipts.json", json.dumps(cached, ensure_ascii=False)
    )
    return cached


def external_receipt(session: Path, url: str, snippet: str | None = None) -> dict:
    row = load(session).get(_canonical(url), {})
    if row.get("state") != "captured":
        return {"state": "uncaptured", "text": ""}
    text = row.get("text", "")
    normalized = lambda x: " ".join(str(x or "").split()).casefold()
    if snippet and normalized(snippet) not in normalized(text):
        return {**row, "state": "snippet_mismatch"}
    return row


def attachment_receipt(scout: dict, source: str) -> dict:
    """attachment:<sha256>:<page>, addressing an extracted, hashed upload."""
    import re

    match = re.fullmatch(r"attachment:([a-f0-9]{64}):([1-9][0-9]*)", str(source))
    if not match:
        return {"state": "invalid_locator"}
    for item in scout.get("attachment_context", {}).get("sources", []):
        if item.get("sha256") == match[1]:
            page = int(match[2])
            pages = item.get("pages") or 1
            if page > pages:
                return {"state": "page_out_of_range"}
            text = item.get("text", "")
            if not text:
                import html

                context = scout.get("attachment_context", {}).get("markdown", "")
                for block in re.finditer(
                    r'<attachment_data name="([^"]+)"[^>]*>(.*?)</attachment_data>',
                    context,
                    re.S,
                ):
                    if html.unescape(block[1]) == item.get("name"):
                        text = block[2]
                        break
            if item.get("truncated"):
                return {"state": "truncated_attachment", "text": text}
            # PDF extraction inserts explicit ## Page N headings.
            parts = re.split(r"(?im)^#{1,4}\s+Page\s+(\d+)\s*$", text)
            if len(parts) > 1:
                text = next(
                    (
                        parts[i + 1]
                        for i in range(1, len(parts) - 1, 2)
                        if int(parts[i]) == page
                    ),
                    "",
                )
            elif page != 1:
                return {"state": "page_unavailable"}
            return {
                "state": "captured" if text else "page_unavailable",
                "text": text,
                "content_sha256": item["sha256"],
                "name": item["name"],
                "page": page,
            }
    return {"state": "missing_attachment"}


def source_excerpt(repo: Path, source: str, *, session: Path, scout: dict) -> dict:
    from decision_map import _local_verification_source_parts, _repo_file_receipt

    if source.startswith("attachment:"):
        return attachment_receipt(scout, source)
    if source.startswith(("http://", "https://")):
        return external_receipt(session, source)
    parts = _local_verification_source_parts(source)
    if not parts:
        return {"state": "invalid_locator", "text": ""}
    name, start, end = parts
    file_hash, line_hash, state = _repo_file_receipt(repo, name, start, end)
    if state != "captured":
        return {"state": state, "text": ""}
    # Receipt helper rejects binary, symlink, sensitive, nonregular/oversize files.
    lines = (repo / name).read_text(errors="replace").splitlines()
    begin, finish = max(1, start - 4), min(len(lines), end + 4)
    return {
        "state": state,
        "file": name,
        "start": begin,
        "end": finish,
        "file_sha256": file_hash,
        "line_sha256": line_hash,
        "text": "\n".join(f"{i}: {lines[i - 1]}" for i in range(begin, finish + 1))[
            :16000
        ],
    }


def evidence_locator(evidence: dict, scout: dict) -> str:
    if evidence.get("type") == "external":
        return str(evidence.get("url") or "")
    if evidence.get("type") == "attachment":
        for item in scout.get("attachment_context", {}).get("sources", []):
            if item.get("name") == evidence.get("file"):
                return f"attachment:{item.get('sha256', '')}:{evidence.get('line', 1)}"
        return ""
    return f"{evidence.get('file')}:{evidence.get('line')}"


def packet(
    repo: Path, rows: list[dict], *, session: Path, scout: dict, limit=65000
) -> str:
    """Compact exact receipts, with omissions explicit and no extra network IO."""
    records, used, seen = [], 0, set()
    for row in rows:
        ev = row.get("evidence", {})
        locator = evidence_locator(ev, scout)
        if locator in seen:
            continue
        seen.add(locator)
        receipt = source_excerpt(repo, locator, session=session, scout=scout)
        if ev.get("type") == "external" and receipt.get("text"):
            text = receipt["text"]
            quote = ev.get("snippet") or ""
            pos = text.casefold().find(quote.casefold()) if quote else -1
            receipt["text"] = (
                text[max(0, pos - 400) : max(0, pos - 400) + 2400]
                if pos >= 0
                else text[:2400]
            )
            receipt["snippet_matched"] = bool(quote and pos >= 0)
        value = {"source": locator, **receipt}
        encoded = json.dumps(value, ensure_ascii=False)
        if used + len(encoded) > limit:
            records.append(
                {
                    "state": "packet_limit",
                    "message": "Additional sources omitted; unshown material remains unverified.",
                }
            )
            break
        records.append(value)
        used += len(encoded)
    return (
        "<captured_source_packet>\n"
        + json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
        + "\n</captured_source_packet>"
    )
