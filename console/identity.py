"""Demo identity lookup (ML-05). The role always comes from seed.app_user, never from the caller (SPEC R9).

This is a demo identity picker, not authentication.
"""
from __future__ import annotations

from ontokit.actions import Actor

UNKNOWN_ROLE = "unknown"


def get_actor(con, user_id: str) -> Actor:
    """Look up a user. An unknown id gives an inactive actor, so every action refuses it (and logs it)."""
    row = con.execute("SELECT user_id, role, active FROM seed.app_user WHERE user_id = ?", [user_id]).fetchone()
    if row is None:
        return Actor(str(user_id), UNKNOWN_ROLE, False)
    return Actor(str(row[0]), str(row[1]), bool(row[2]))
