"""YAML ontology loader and backing check (ML-02, AC-3.1, ADR 005).

Domain-free: this module must not import pipeline, console or app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CARDINALITIES = frozenset({"1-1", "1-*", "*-1", "*-*"})


class OntologyError(ValueError):
    """The ontology file is malformed or references something that does not exist."""


@dataclass(frozen=True)
class ObjectType:
    name: str
    key: list[str]
    view: str
    synthetic: bool
    properties: dict[str, str]
    source: str | None = None          # pipeline-owned base table
    edits: str | None = None           # append-only edit table (edits win)
    defaults: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkType:
    name: str
    from_type: str
    to_type: str
    cardinality: str
    view: str
    from_key: str = ""
    to_key: str = ""
    sql: str = ""


@dataclass(frozen=True)
class ActionType:
    name: str
    object_type: str
    roles: frozenset[str]
    criteria: list[str]


@dataclass(frozen=True)
class Ontology:
    objects: dict[str, ObjectType]
    links: dict[str, LinkType]
    actions: dict[str, ActionType]
    roles: frozenset[str] = frozenset()


def _req(d: dict, k: str, where: str):
    if not isinstance(d, dict) or k not in d or d[k] in (None, "", []):
        raise OntologyError(f"{where}: missing `{k}`")
    return d[k]


def parse_ontology(data: dict) -> Ontology:
    """Build and validate an Ontology from parsed YAML. Raises OntologyError."""
    if not isinstance(data, dict):
        raise OntologyError("ontology root must be a mapping")
    roles = frozenset(data.get("roles") or [])
    objects: dict[str, ObjectType] = {}
    views: set[str] = set()
    for name, o in (_req(data, "objects", "root") or {}).items():
        where = f"object {name}"
        key = _req(o, "key", where)
        if isinstance(key, str):
            key = [key]
        view = _req(o, "view", where)
        if not o.get("source") and not o.get("edits"):
            raise OntologyError(f"{where}: needs `source` or `edits`")
        props = dict(o.get("properties") or {})
        missing = [k for k in key if props and k not in props]
        if missing:
            raise OntologyError(f"{where}: key {missing} not in properties")
        if view in views:
            raise OntologyError(f"{where}: view {view} declared twice")
        views.add(view)
        objects[name] = ObjectType(name, list(key), view, bool(o.get("synthetic", False)), props,
                                   o.get("source"), o.get("edits"), dict(o.get("defaults") or {}))

    links: dict[str, LinkType] = {}
    for name, ln in (data.get("links") or {}).items():
        where = f"link {name}"
        frm, to = _req(ln, "from", where), _req(ln, "to", where)
        for t in (frm, to):
            if t not in objects:
                raise OntologyError(f"{where}: unknown object type {t!r}")
        card = str(_req(ln, "cardinality", where))
        if card not in CARDINALITIES:
            raise OntologyError(f"{where}: bad cardinality {card!r}")
        view = _req(ln, "view", where)
        if view in views:
            raise OntologyError(f"{where}: view {view} declared twice")
        views.add(view)
        links[name] = LinkType(name, frm, to, card, view, ln.get("from_key", ""), ln.get("to_key", ""),
                               str(ln.get("sql") or "").strip())

    actions: dict[str, ActionType] = {}
    for name, a in (data.get("actions") or {}).items():
        where = f"action {name}"
        ot = _req(a, "object_type", where)
        if ot not in objects:
            raise OntologyError(f"{where}: unknown object type {ot!r}")
        ar = frozenset(_req(a, "roles", where))
        if roles and not ar <= roles:
            raise OntologyError(f"{where}: unknown roles {sorted(ar - roles)}")
        crit = list(a.get("criteria") or [])
        if len(set(crit)) != len(crit):
            raise OntologyError(f"{where}: duplicate criterion ids")
        if "role" in crit:
            raise OntologyError(f"{where}: criterion id 'role' is reserved for the runner")
        actions[name] = ActionType(name, ot, ar, crit)
    return Ontology(objects, links, actions, roles)


def load_ontology(path: Path) -> Ontology:
    """Load ontology YAML and validate its references. Raises OntologyError."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OntologyError(f"{path}: invalid YAML: {exc}") from exc
    return parse_ontology(data)


def _split(view: str) -> tuple[str, str]:
    schema, _, name = view.rpartition(".")
    return (schema or "main"), name


def _columns(con, view: str) -> list[str] | None:
    schema, name = _split(view)
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
        "ORDER BY ordinal_position", [schema, name]).fetchall()
    return [r[0] for r in rows] or None


def check_backing(con, onto: Ontology) -> list[str]:
    """Return a list of problems: missing view, missing key or link columns, null or duplicate keys."""
    problems: list[str] = []
    for o in onto.objects.values():
        cols = _columns(con, o.view)
        if cols is None:
            problems.append(f"{o.name}: view {o.view} is missing")
            continue
        missing = [c for c in list(o.key) + list(o.properties) if c not in cols]
        if missing:
            problems.append(f"{o.name}: {o.view} lacks columns {missing}")
            continue
        keys = ", ".join(f'"{k}"' for k in o.key)
        nulls = con.execute(
            f"SELECT count(*) FROM {o.view} WHERE " + " OR ".join(f'"{k}" IS NULL' for k in o.key)).fetchone()[0]
        if nulls:
            problems.append(f"{o.name}: {nulls} rows with a null key in {o.view}")
        dups = con.execute(
            f"SELECT count(*) FROM (SELECT {keys} FROM {o.view} GROUP BY {keys} HAVING count(*) > 1)").fetchone()[0]
        if dups:
            problems.append(f"{o.name}: {dups} duplicate keys in {o.view}")
    for ln in onto.links.values():
        cols = _columns(con, ln.view)
        if cols is None:
            problems.append(f"link {ln.name}: view {ln.view} is missing")
            continue
        missing = [c for c in (ln.from_key, ln.to_key) if c and c not in cols]
        if missing:
            problems.append(f"link {ln.name}: {ln.view} lacks columns {missing}")
    return problems
