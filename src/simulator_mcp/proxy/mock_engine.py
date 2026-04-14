"""Mock rule engine for intercepting and replacing HTTP responses."""

import re
import threading
from dataclasses import dataclass, field


@dataclass
class MockRule:
    id: str
    url_pattern: str
    method: str | None = None  # None matches any method
    status_code: int = 200
    response_headers: dict = field(default_factory=lambda: {"Content-Type": "application/json"})
    response_body: str = ""
    response_body_file: str | None = None
    _compiled_pattern: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self):
        try:
            self._compiled_pattern = re.compile(self.url_pattern)
        except re.error as exc:
            raise ValueError(f"Invalid url_pattern {self.url_pattern!r}: {exc}") from exc

    def matches(self, request_url: str, request_method: str) -> bool:
        if self.method and self.method.upper() != request_method.upper():
            return False
        return bool(self._compiled_pattern.search(request_url))

    def get_response_body(self) -> str:
        if self.response_body_file:
            try:
                with open(self.response_body_file, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
        return self.response_body

    def to_dict(self) -> dict:
        body = self.response_body
        return {
            "id": self.id,
            "url_pattern": self.url_pattern,
            "method": self.method,
            "status_code": self.status_code,
            "response_headers": self.response_headers,
            "response_body": body[:200] + "..." if len(body) > 200 else body,
            "response_body_file": self.response_body_file,
        }


class MockEngine:
    def __init__(self):
        self._rules: dict[str, MockRule] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def add_rule(
        self,
        url_pattern: str,
        method: str | None = None,
        status_code: int = 200,
        response_headers: dict | None = None,
        response_body: str = "",
        response_body_file: str | None = None,
    ) -> MockRule:
        with self._lock:
            rule = MockRule(
                id=f"mock_{self._next_id}",
                url_pattern=url_pattern,
                method=method,
                status_code=status_code,
                response_headers=response_headers or {"Content-Type": "application/json"},
                response_body=response_body,
                response_body_file=response_body_file,
            )
            self._rules[rule.id] = rule
            self._next_id += 1
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        with self._lock:
            return self._rules.pop(rule_id, None) is not None

    def find_match(self, url: str, method: str) -> MockRule | None:
        with self._lock:
            rules = tuple(self._rules.values())
        for rule in rules:
            if rule.matches(url, method):
                return rule
        return None

    def list_rules(self) -> list[dict]:
        with self._lock:
            rules = tuple(self._rules.values())
        return [rule.to_dict() for rule in rules]
