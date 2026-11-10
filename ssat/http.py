"""A very small JSON over HTTP helper on top of urllib.

The transport is a plain function, so tests and the demo can swap it for a
fake one and nothing touches the network. Both APIs here allow 5 requests per
second and ask for a 30 second pause after a 429, so the client spaces its
calls out and backs off the way the docs say.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlencode

Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]


class ApiError(Exception):
    """An API answered with an error. The message never includes headers,
    so tokens can't leak into logs."""

    def __init__(self, service: str, method: str, path: str, status: int, detail: str):
        self.status = status
        super().__init__(f"{service} {method} {path} failed with HTTP {status}: {detail}")


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class JsonClient:
    def __init__(self, service: str, base_url: str, headers: dict, transport: Transport | None = None,
                 max_retries: int = 4, min_interval: float = 0.21, backoff_429: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self._headers = headers  # holds a token, never printed
        self.transport = transport or urllib_transport
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.backoff_429 = backoff_429
        self.sleep = sleep
        self.clock = clock
        self._last = -1e9
        self.calls = 0

    def request(self, method: str, path: str, *, params=None, body=None):
        url = self.base_url + path
        if params:
            pairs = params.items() if isinstance(params, dict) else params
            url += "?" + urlencode([(k, v) for k, v in pairs if v is not None])
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self._headers)
        headers["Accept"] = "application/json"
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
            self.calls += 1
            status, resp_headers, raw = self.transport(method, url, headers, data)
            if (status == 429 or status >= 500) and attempt < self.max_retries:
                self.sleep(self._retry_after(resp_headers, status, attempt))
                continue
            if status >= 400:
                raise ApiError(self.service, method, path.split("?")[0], status, error_detail(raw))
            if not raw or not raw.strip():
                return None
            return json.loads(raw)
        raise AssertionError("unreachable")

    def _retry_after(self, headers: dict, status: int, attempt: int) -> float:
        for k, v in (headers or {}).items():
            if k.lower() == "retry-after":
                try:
                    return min(float(v), 120.0)
                except ValueError:
                    break
        return self.backoff_429 if status == 429 else min(2 ** attempt, 30)


def error_detail(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return raw[:200].decode(errors="replace") if raw else "no body"
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):  # Airtable
            return f"{err.get('type')}: {err.get('message')}"
        if err or payload.get("err"):
            return str(err or payload.get("err"))[:200]
    return json.dumps(payload)[:200]  # SmartSuite validation errors are keyed by field
