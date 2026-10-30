"""Render the README infographics from run outputs. Owner: principal-engineer.

Reads reports/match_quality_v1.json, _v2.json, _v3.json (frozen per-round reports) and the holdout CSVs.
The per-category split (single ingredient / combination / premix) is a post-hoc classification by the
Principal Engineer from the formulary item text; it is listed below so anyone can check it. It is not a
frozen label and was not used to tune anything. Run: python docs/img/make_charts.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984"

# Post-hoc category of each POSITIVE pair (by formulary item text). Everything not listed is single-ingredient.
COMBO = {
    "v1": {"GP-036", "GP-037", "GP-049", "GP-050"},
    "v2": {"HP-005", "HP-013", "HP-051"},
    "v3": {"VP-001", "VP-004", "VP-006", "VP-009", "VP-010", "VP-013", "VP-014", "VP-016", "VP-022", "VP-037",
           "VP-043", "VP-045", "VP-048", "VP-050", "VP-051", "VP-054", "VP-056", "VP-058", "VP-059", "VP-062",
           "VP-064", "VP-088", "VP-090"},
}
PREMIX = {"v1": set(), "v2": set(), "v3": {"VP-024", "VP-030", "VP-032", "VP-035"}}
SETS = {
    "v1": ("reports/match_quality_v1.json", None, "tests/fixtures/gold_matches.csv",
           "v1 matcher\ngold set (blind then)"),
    "v2": ("reports/match_quality_v2.json", "holdout_unseen", "tests/fixtures/holdout_v2.csv",
           "v2 matcher\nunseen holdout"),
    "v3": ("reports/match_quality_v3.json", "holdout_v3_unseen", "tests/fixtures/holdout_v3.csv",
           "v3 matcher\nunseen, harder set"),
}


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def load(key):
    rep, block, gold, _ = SETS[key]
    d = json.loads((ROOT / rep).read_text(encoding="utf-8"))
    d = d[block] if block else d
    fails = {p["pair_id"] for p in d["matcher"]["failing_pairs"] if p["error"] == "FN"}
    lines = [ln for ln in (ROOT / gold).read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    pos = [r["pair_id"] for r in csv.DictReader(lines) if r["label"] == "1"]
    return d["matcher"]["overall"], pos, fails


def category_rows():
    rows = {}
    for k in SETS:
        overall, pos, fails = load(k)
        assert len(fails) == overall["fn"], (k, len(fails), overall["fn"])
        cats = {"Single ingredient": [], "Combination": [], "Premix (drug in D5W / saline)": []}
        for p in pos:
            c = ("Combination" if p in COMBO[k] else "Premix (drug in D5W / saline)" if p in PREMIX[k]
                 else "Single ingredient")
            cats[c].append(p)
        rows[k] = {c: (len(v), sum(1 for p in v if p not in fails)) for c, v in cats.items()}
    return rows


def chart_rounds():
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150, facecolor=SURFACE)
    style(ax)
    xs = range(len(SETS))
    w = 0.34
    for i, k in enumerate(SETS):
        o, _, _ = load(k)
        for off, val, col, lab in ((-w / 2, o["precision"], BLUE, "Precision"),
                                   (w / 2, o["recall"], ORANGE, "Recall")):
            ax.bar(i + off, val, w - 0.04, color=col, label=lab if i == 0 else None, zorder=2)
            ax.text(i + off, val + 0.015, f"{val:.3f}", ha="center", va="bottom", fontsize=9, color=INK)
    ax.axhline(0.95, color=INK2, linestyle="--", linewidth=1)
    ax.text(2.55, 0.955, "recall target 0.95", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.set_xticks(list(xs), [SETS[k][3] for k in SETS], color=INK, fontsize=9)
    ax.set_ylim(0, 1.18)
    ax.set_title("Precision held near 0.96; recall fell on the harder set", loc="left", fontsize=12, color=INK)
    ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=9, labelcolor=INK)
    fig.text(0.01, 0.01, "Each matcher scored once on a set its author never opened. Labels team-drafted,"
             " review pending."
             " Source: reports/match_quality_v1/v2/v3.json", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "matcher_rounds.png", facecolor=SURFACE)


def chart_categories(rows):
    cats = ["Single ingredient", "Combination", "Premix (drug in D5W / saline)"]
    cols = [BLUE, ORANGE, AQUA]
    fig, ax = plt.subplots(figsize=(8, 4.4), dpi=150, facecolor=SURFACE)
    style(ax)
    w = 0.26
    for i, k in enumerate(SETS):
        for j, (c, col) in enumerate(zip(cats, cols, strict=True)):
            n, hit = rows[k][c]
            x = i + (j - 1) * w
            if n == 0:
                ax.text(x, 0.02, "none\nin set", ha="center", va="bottom", fontsize=7.5, color=INK2)
                continue
            r = hit / n
            ax.bar(x, max(r, 0.004), w - 0.04, color=col, label=c if i == 2 else None, zorder=2)
            ax.text(x, r + 0.015, f"{r:.2f}\n{hit}/{n}", ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(range(3), [SETS[k][3] for k in SETS], color=INK, fontsize=9)
    ax.set_ylim(0, 1.22)
    ax.set_title("Recall by product type: the v3 set was 53% combinations", loc="left", fontsize=12, color=INK)
    ax.legend(frameon=False, loc="upper right", fontsize=8.5, ncol=3, labelcolor=INK)
    fig.text(0.01, 0.01, "Category assigned post hoc from the item text (list in docs/img/make_charts.py)."
             " Hits/positives per category.", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "recall_by_category.png", facecolor=SURFACE)


def chart_day_one():
    # ADR 007 table (live DB, 2026-10-30) and docs/impact.md
    steps = [("Matched + stocked events,\noriginal day-one rule", 663),
             ("...actionable (not Resolved;\nLimited/Unavailable or to be discontinued)", 262),
             ("One-time baseline worklist,\none row per formulary item", 73),
             ("Rows on screen at once\n(visible cap, 'N more' shown)", 40)]
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150, facecolor=SURFACE)
    style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ys = list(range(len(steps)))[::-1]
    for y, (_lab, v) in zip(ys, steps, strict=True):
        ax.barh(y, v, 0.6, color=BLUE if v != 663 else GRAY, zorder=2)
        ax.text(v + 8, y, str(v), va="center", fontsize=9, color=INK)
    ax.set_yticks(ys, [s[0] for s in steps], fontsize=8.5, color=INK)
    ax.set_xlim(0, 740)
    fig.suptitle("Day one on live openFDA data: 663 events became a 73-item review list", x=0.01, ha="left",
                 fontsize=11.5, color=INK)
    fig.text(0.01, 0.01, "Live public openFDA snapshot 2026-10-30 against the SYNTHETIC formulary. Source: ADR 007,"
             " docs/impact.md", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(OUT / "day_one_queue.png", facecolor=SURFACE)


if __name__ == "__main__":
    rows = category_rows()
    print(json.dumps(rows, indent=1))
    chart_rounds()
    chart_categories(rows)
    chart_day_one()
