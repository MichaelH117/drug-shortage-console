"""The ONLY network code in the project (owner: data-engineer, AC-1.3, DESIGN section 4).

Every call goes through ``UrllibClient.get_json``, which checks the host against ``ALLOWED_HOSTS`` before opening a
connection, and refuses redirects to any other host. Stdlib ``urllib`` only (DESIGN section 7). openFDA needs no API
key, so there is no secret here.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Protocol

ALLOWED_HOSTS = frozenset({"api.fda.gov"})
USER_AGENT = "drug-shortage-console/0.1 (portfolio project; synthetic hospital)"


class HostNotAllowed(ValueError):
    """Raised before any connection is opened when a URL's host is not on the allow-list."""


class HttpClient(Protocol):
    def get_json(self, url: str, params: dict) -> dict: ...


def check_host(url: str) -> str:
    """Return ``url`` if it is https, on port 443, and its host is allowed; raise ``HostNotAllowed`` otherwise."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:            # a non-numeric port
        port = -1
    if (parts.scheme != "https" or host not in ALLOWED_HOSTS or parts.username or parts.password
            or port not in (None, 443)):
        raise HostNotAllowed(f"refusing to call {url!r}: only https hosts {sorted(ALLOWED_HOSTS)} are allowed")
    return url


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect to a host that is not allowed, before following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibClient:
    """Minimal JSON GET client. The host is checked on every call, before any socket is opened."""

    def __init__(self, timeout_s: float = 30):
        self.timeout_s = timeout_s
        self.calls = 0
        # ProxyHandler({}) replaces the default one, so HTTP(S)_PROXY env vars cannot route traffic elsewhere.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _CheckedRedirect)

    def get_json(self, url: str, params: dict) -> dict:
        check_host(url)
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        check_host(full)
        req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.calls += 1
        with self._opener.open(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))


def default_client(timeout_s: float = 30) -> HttpClient:
    return UrllibClient(timeout_s=timeout_s)
