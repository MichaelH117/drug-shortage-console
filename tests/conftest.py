"""Shared fixtures (owner: data-engineer, DESIGN section 6).

* A socket guard on EVERY test: ``socket.socket.connect`` raises, so no test can reach the network (AC-1.4).
  Tests marked ``@pytest.mark.live`` run only with ``DSC_LIVE=1`` and are the only ones the guard lets through.
* ``mem_db``: an empty in-memory DuckDB (RAM limits applied) for unit tests.
* ``demo_db``: one fixtures-mode ``build()`` per session into a tmp folder; the repo's data/ is never touched.
"""

from __future__ import annotations

import os
import socket

import pytest

from pipeline.config import connect, paths_for

LIVE = os.environ.get("DSC_LIVE") == "1"


class NetworkBlocked(RuntimeError):
    """A test tried to open a network connection (AC-1.4)."""


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live test: set DSC_LIVE=1 to call api.fda.gov")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def socket_guard(request, monkeypatch):
    if LIVE and "live" in request.keywords:
        yield
        return

    def _blocked(self, address, *args, **kwargs):
        raise NetworkBlocked(f"network access is blocked in tests (tried {address!r})")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", lambda address, *a, **k: _blocked(None, address))
    yield


@pytest.fixture
def mem_db():
    con = connect(":memory:")
    yield con
    con.close()


@pytest.fixture(scope="session")
def demo_build(tmp_path_factory):
    """``{"paths": Paths, "summary": dict}`` for one offline fixtures build into a tmp folder."""
    from pipeline.run import build

    base = tmp_path_factory.mktemp("demo")
    paths = paths_for("demo", base)
    summary = build(paths, client=None, refresh=False, seed=42)
    return {"paths": paths, "summary": summary}


@pytest.fixture(scope="session")
def demo_db(demo_build):
    """Read-only connection to the session's fixtures-mode build (DESIGN section 6)."""
    con = connect(demo_build["paths"].db, read_only=True)
    yield con
    con.close()
