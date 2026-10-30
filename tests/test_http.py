"""DE-01 / DE-03: the offline guard and the host allow-list (AC-1.3, AC-1.4)."""

from __future__ import annotations

import socket
import urllib.request

import pytest

from pipeline.http import ALLOWED_HOSTS, HostNotAllowed, UrllibClient, check_host


def test_socket_guard_blocks_a_real_connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="network access is blocked"):
            s.connect(("api.fda.gov", 443))
    finally:
        s.close()


def test_socket_guard_blocks_urllib_even_for_the_allowed_host():
    with pytest.raises(Exception, match="blocked"):
        urllib.request.urlopen("https://api.fda.gov/drug/shortages.json?limit=1", timeout=2)


def test_only_api_fda_gov_is_allowed():
    assert ALLOWED_HOSTS == frozenset({"api.fda.gov"})
    assert check_host("https://api.fda.gov/drug/shortages.json")


@pytest.mark.parametrize("url", [
    "https://example.com/drug/shortages.json",
    "https://api.fda.gov.evil.com/x",
    "https://evil.com/?u=https://api.fda.gov",
    "http://api.fda.gov/drug/shortages.json",           # plain http refused
    "https://user:pw@api.fda.gov/drug/shortages.json",  # credentials in the URL refused
    "ftp://api.fda.gov/x",
    "https://api.fda.gov:8443/drug/shortages.json",     # REVIEW #12: a non-443 port is refused
    "https://api.fda.gov:80/drug/shortages.json",
])
def test_other_hosts_are_refused(url):
    with pytest.raises(HostNotAllowed):
        check_host(url)


def test_client_refuses_before_opening_a_socket():
    client = UrllibClient()
    with pytest.raises(HostNotAllowed):
        client.get_json("https://example.com/api", {"limit": 1})
    assert client.calls == 0


def test_explicit_port_443_is_allowed():
    assert check_host("https://api.fda.gov:443/drug/shortages.json")


def test_proxy_env_vars_are_ignored(monkeypatch):
    """REVIEW #12: an HTTPS_PROXY env var must not route traffic to another host."""
    monkeypatch.setenv("HTTPS_PROXY", "http://evil.example:3128")
    monkeypatch.setenv("https_proxy", "http://evil.example:3128")
    default = urllib.request.build_opener()     # control: the stdlib default WOULD pick the env proxy up
    assert any(getattr(h, "proxies", {}) for h in default.handlers)
    client = UrllibClient()
    assert not any(getattr(h, "proxies", {}) for h in client._opener.handlers)
