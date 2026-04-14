"""In-memory and persisted network request/response log storage."""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

SUMMARY_LOG_FILE = "/tmp/proxy_requests.log"
DETAIL_LOG_FILE = "/tmp/proxy_requests.jsonl"
BODY_LOG_DIR = "/tmp/proxy_request_bodies"
BODY_INLINE_LIMIT = 16 * 1024


@dataclass
class LogEntry:
    id: int
    timestamp: float
    method: str
    url: str
    status_code: int | None = None
    error: str | None = None
    request_headers: dict = field(default_factory=dict)
    response_headers: dict = field(default_factory=dict)
    request_body: str | None = None
    response_body: str | None = None
    request_body_file: str | None = None
    response_body_file: str | None = None
    duration_ms: float | None = None
    mocked: bool = False

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "timestamp": self.timestamp,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "error": self.error,
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
            "request_body": self.request_body,
            "response_body": self.response_body,
            "request_body_file": self.request_body_file,
            "response_body_file": self.response_body_file,
            "duration_ms": self.duration_ms,
        }
        if self.mocked:
            d["mocked"] = True
        return d


class NetworkLog:
    def __init__(
        self,
        max_entries: int = 1000,
        summary_log_file: str = SUMMARY_LOG_FILE,
        detail_log_file: str = DETAIL_LOG_FILE,
        body_log_dir: str = BODY_LOG_DIR,
        body_inline_limit: int = BODY_INLINE_LIMIT,
    ):
        self._entries: list[LogEntry] = []
        self._next_id = 1
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._summary_log_file = summary_log_file
        self._detail_log_file = detail_log_file
        self._body_log_dir = body_log_dir
        self._body_inline_limit = body_inline_limit

    def add(self, **kwargs) -> LogEntry:
        with self._lock:
            entry = LogEntry(id=self._next_id, timestamp=time.time(), **kwargs)
            self._next_id += 1
            self._spill_large_body_to_file(entry, "request")
            self._spill_large_body_to_file(entry, "response")
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
        try:
            self._append_summary_log(entry)
            self._append_detail_log(entry)
        except Exception:
            pass
        return entry

    def _spill_large_body_to_file(self, entry: LogEntry, kind: str) -> None:
        body_attr = f"{kind}_body"
        body_file_attr = f"{kind}_body_file"
        body = getattr(entry, body_attr)
        if body is None or len(body.encode("utf-8")) <= self._body_inline_limit:
            return

        os.makedirs(self._body_log_dir, exist_ok=True)
        timestamp_ms = int(entry.timestamp * 1000)
        body_file = os.path.join(self._body_log_dir, f"{timestamp_ms}_{entry.id}_{kind}.txt")
        with open(body_file, "w", encoding="utf-8") as f:
            f.write(body)

        setattr(entry, body_file_attr, body_file)
        setattr(entry, body_attr, None)

    def _append_summary_log(self, entry: LogEntry) -> None:
        parsed = urlparse(entry.url)
        dur = f"{entry.duration_ms:.0f}ms" if entry.duration_ms else "?"
        status = entry.status_code or "?"
        mock_tag = " [MOCK]" if entry.mocked else ""
        line = f"{time.strftime('%H:%M:%S')} {status} {entry.method:5} {dur:>8}  {parsed.netloc}{parsed.path}{mock_tag}"
        summary_dir = os.path.dirname(self._summary_log_file)
        if summary_dir:
            os.makedirs(summary_dir, exist_ok=True)
        with open(self._summary_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _append_detail_log(self, entry: LogEntry) -> None:
        detail_dir = os.path.dirname(self._detail_log_file)
        if detail_dir:
            os.makedirs(detail_dir, exist_ok=True)
        with open(self._detail_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    @staticmethod
    def _matches(entry: dict, url_pattern: str | None, method: str | None) -> bool:
        if url_pattern and url_pattern not in entry["url"]:
            return False
        if method and entry["method"].upper() != method.upper():
            return False
        return True

    def _query_persisted(
        self,
        url_pattern: str | None = None,
        method: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if not os.path.exists(self._detail_log_file):
            return []

        results = []
        with open(self._detail_log_file, encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not self._matches(entry, url_pattern, method):
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def query(
        self,
        url_pattern: str | None = None,
        method: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        with self._lock:
            entries = list(self._entries)
        results = []
        for entry in reversed(entries):
            entry_dict = entry.to_dict()
            if not self._matches(entry_dict, url_pattern, method):
                continue
            results.append(entry_dict)
            if len(results) >= limit:
                break
        if results:
            return results
        return self._query_persisted(url_pattern=url_pattern, method=method, limit=limit)

    def clear(self):
        with self._lock:
            self._entries.clear()
        for path in (self._summary_log_file, self._detail_log_file):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        if os.path.isdir(self._body_log_dir):
            for filename in os.listdir(self._body_log_dir):
                try:
                    os.remove(os.path.join(self._body_log_dir, filename))
                except FileNotFoundError:
                    pass
