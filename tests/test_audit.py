"""DE-08: the append-only, hash-chained action log (AC-7.1, AC-7.2, AC-7.3, AC-7.5)."""

from __future__ import annotations

import datetime as dt

import pytest

from ontokit import audit
from pipeline.config import ROOT

T0 = dt.datetime(2026, 10, 30, 12, 0, 0)


def _append(con, i, outcome="SUCCESS"):
    return audit.append(con, action_type="approve_substitute", object_type="Substitute", object_key=f"S-{i:03d}",
                        actor_id="dana.ruiz", role="pharmacist", reason=f"reason {i}",
                        old_value={"state": "proposed"}, new_value={"state": "approved", "n": i},
                        outcome=outcome, failed_criterion=None if outcome == "SUCCESS" else "role",
                        now=T0 + dt.timedelta(minutes=i))


@pytest.fixture
def log(mem_db):
    audit.init_audit(mem_db)
    for i in range(1, 6):
        _append(mem_db, i, "SUCCESS" if i % 2 else "REFUSED")
    return mem_db


def test_clean_chain_is_ok(log):
    r = audit.verify_chain(log)
    assert r.ok and r.rows_checked == 5 and r.first_broken_seq is None
    assert isinstance(r.checked_at_utc, dt.datetime)


def test_columns_follow_ac_7_1(log):
    cols = [c[0] for c in log.execute("DESCRIBE audit.action_log").fetchall()]
    assert cols == ["seq", "action_type", "object_type", "object_key", "actor_id", "role", "reason", "old_value",
                    "new_value", "outcome", "failed_criterion", "ts_utc", "prev_hash", "row_hash"]


def test_genesis_and_hash_rule(log):
    first = log.execute("SELECT * FROM audit.action_log ORDER BY seq LIMIT 1").fetchone()
    assert first[12] == audit.GENESIS_HASH == "0" * 64
    import hashlib

    row = dict(zip(audit.LOG_COLUMNS, first, strict=True))
    content = audit.canonical_json({c: row[c] for c in audit.HASHED_COLUMNS})
    assert row["row_hash"] == hashlib.sha256(f"{'0' * 64}|{content}".encode()).hexdigest()


def test_append_returns_next_seq_and_writes_a_checkpoint(log):
    assert _append(log, 6) == 6
    assert log.execute("SELECT max(head_seq), count(*) FROM audit.chain_checkpoint").fetchone() == (6, 6)


def test_update_and_delete_raise(log):
    with pytest.raises(audit.AppendOnlyError):
        audit.update(log, seq=1)
    with pytest.raises(audit.AppendOnlyError):
        audit.delete(log, seq=1)


def test_refused_row_needs_a_criterion(mem_db):
    audit.init_audit(mem_db)
    with pytest.raises(ValueError):
        audit.append(mem_db, action_type="a", object_type="o", object_key="k", actor_id="u", role="r", reason=None,
                     old_value=None, new_value=None, outcome="REFUSED")


@pytest.mark.parametrize("k", [1, 3, 5])
def test_direct_sql_edit_of_row_k_is_found_at_k(log, k):
    log.execute("UPDATE audit.action_log SET reason = 'edited' WHERE seq = ?", [k])
    r = audit.verify_chain(log)
    assert not r.ok and r.first_broken_seq == k and r.reason.startswith("CHAIN")


def test_deleting_a_middle_row_is_found(log):
    log.execute("DELETE FROM audit.action_log WHERE seq = 3")
    r = audit.verify_chain(log)
    assert not r.ok and r.first_broken_seq == 4


def test_deleting_the_newest_row_is_caught_by_the_checkpoint(log):
    log.execute("DELETE FROM audit.action_log WHERE seq = 5")
    r = audit.verify_chain(log)
    assert not r.ok and r.first_broken_seq == 5 and r.reason.startswith("CHECKPOINT")


def test_anchor_catches_deletion_even_when_checkpoints_are_rewritten(log, tmp_path):
    anchor = audit.write_anchor(log, tmp_path / "anchor.json")
    assert anchor["head_seq"] == 5
    log.execute("DELETE FROM audit.action_log WHERE seq = 5")
    log.execute("DELETE FROM audit.chain_checkpoint WHERE head_seq = 5")
    assert audit.verify_chain(log).ok            # the in-database checks alone are fooled
    r = audit.verify_chain(log, anchor=tmp_path / "anchor.json")
    assert not r.ok and r.first_broken_seq == 5 and r.reason.startswith("ANCHOR")


def test_rows_appended_outside_append_are_caught(log):
    log.execute("INSERT INTO audit.action_log SELECT seq + 1, action_type, object_type, object_key, actor_id, role, "
                "reason, old_value, new_value, outcome, failed_criterion, ts_utc, row_hash, row_hash "
                "FROM audit.action_log WHERE seq = 5")
    assert not audit.verify_chain(log).ok


def test_caller_owns_the_transaction(log):
    log.begin()
    _append(log, 6)
    log.rollback()
    assert log.execute("SELECT count(*) FROM audit.action_log").fetchone()[0] == 5
    assert audit.verify_chain(log).ok


def test_source_is_named_at_the_top():
    head = (ROOT / "ontokit" / "audit.py").read_text(encoding="utf-8")[:600]
    assert "vaccine-assay-pipeline" in head and "audit.py" in head


def test_concurrent_appends_under_writer_lock_keep_one_chain(tmp_path):
    """Two threads, separate connections to one file, 20 appends each: no seq clash, one valid chain."""
    import threading

    from pipeline.config import connect

    con = connect(tmp_path / "a.duckdb")
    audit.init_audit(con)
    errors = []

    def worker(n):
        cur = con.cursor()
        try:
            for i in range(20):
                with audit.writer_lock():
                    cur.execute("BEGIN TRANSACTION")
                    _append(cur, n * 100 + i)
                    cur.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            cur.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    try:
        assert errors == []
        r = audit.verify_chain(con)
        assert r.ok and r.rows_checked == 40
    finally:
        con.close()
