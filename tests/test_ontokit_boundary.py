"""ADR 005: ontokit must not import pipeline, console or app."""
import ast
from pathlib import Path

FORBIDDEN = {"pipeline", "console", "app"}
ONTOKIT = Path(__file__).resolve().parents[1] / "ontokit"


def test_ontokit_imports_nothing_domain_specific():
    offenders = []
    files = sorted(ONTOKIT.glob("*.py"))
    assert files, "ontokit has no modules"
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative imports stay inside ontokit
                    continue
                names = [node.module or ""]
            else:
                continue
            offenders += [f"{f.name}: {n}" for n in names if n.split(".")[0] in FORBIDDEN]
    assert offenders == []
