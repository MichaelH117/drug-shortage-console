# Design review: Hanson Labs / FORMULARY restyle

2026-10-30, Design Lead. Step 4 of `dev-team/OVERNIGHT_PLAN.md`. This is a restyle only. Pipeline, matching, queries and actions are unchanged, and every number on screen still comes from the same `console.*` calls.

## Context (three lines)
- **Who.** Hospital and independent pharmacists. The median age is about 41 and many are 50+. They often stand at a shared counter and get interrupted 5–20 times an hour (research/audience-data A2).
- **What they must know.** Which shortages since yesterday hit drugs we stock, and which of them still need a decision.
- **What they should do.** Open the top item, approve or reject a substitute, and open a task. Then get back to where they were after the next interruption.

## Before and after

| | Before (commit 206b2b4) | After (this commit) |
|---|---|---|
| Theme | A dark "ops console" in Streamlit's default sans, 12–13 px meta text, and grey-on-charcoal captions. | Light FORMULARY: white paper with an `#F4F5F2` sidebar and ink `#1B1B1B`. All colours come from the brand tokens, and every text pair is 4.5:1 or better. |
| Type | Default Source Sans, with ALL-CAPS 11 px labels. | Atkinson Hyperlegible Next for body text at a 17 px base, Source Serif 4 for headings, and IBM Plex Mono for dates, NDCs and figures. The tiny caps labels are gone. |
| Status | Solid coloured pills (red/blue/green) where the colour carried most of the meaning. | **Pharmacy auxiliary-label stickers.** Each one has an icon band (the shape) plus the word: NEW `+`, WORSENED `↘`, UNAVAILABLE octagon-x, LIMITED triangle, RESOLVED/AVAILABLE/SUCCESS check, REFUSED/FAIL octagon-x. The object view gets a large label with a top band and a plain instruction ("FDA lists it as unavailable. Substitutes are below; a pharmacist decides."). |
| Figures | Six identical boxed KPI cards (an AI-look tell). | One lead number in large serif type. The other five figures sit in a quiet ruled ledger with a 2 px ink rule on top. Colour appears only where the number is itself a status (lowest days on hand ≤ 7 in brick, "needs a decision" in amber, FAIL in brick). |
| Takeaway | The page opened on a "Business question" line. | A one-sentence takeaway band is built from the live numbers: "3 shortages hit drugs Riverbend stocks. 1 needs a decision." |
| Brand | None. | Hanson Labs wordmark in the sidebar (monogram when collapsed), the monogram favicon, and the "A Hanson Labs project" footer lockup with the data-source line. |
| Wallpaper | None. | The brand engine with the FORMULARY motif (capsules, tablets, label stickers) runs **only inside the Home overview band, behind a solid paper panel, and is OFF by default.** A toggle turns it on (`?bg=1` also works for bookmarks), and it shows the brand's 44 px Pause motion button. The Drug view, Audit, action forms, Metrics and Match Quality never load it. A test covers this. |
| Interruptions | `?event=` on the Drug page only. | `?as=` is now written back, so a reload keeps the identity. The sidebar has a **"Where you were"** button that reopens the last shortage in one click from any page. |
| Phone | The sidebar opened over the content. | The sidebar collapses (`auto`), the layout is one column with a 16 px gutter, and touch targets are at least 44 px. |

Screenshots:
- **After:** `docs/demo/01..06*.png` (1440 px), `docs/demo/mobile/*_375.png`, and `docs/demo/01b_queue_background_on.png`.
- **Before:** `git show 206b2b4:docs/demo/01_queue.png` (the same applies to 02–06).

AI-look tells removed: the default Streamlit look, identical cards, dark mode with grey low-contrast body text, long ALL-CAPS labels, and coloured fills used as the only status cue. There were no emoji icons to remove (none were found in `app/`).

## Chartability fast pass (pages, not a full audit)

| Test | Result |
|---|---|
| Text contrast ≥ 4.5:1 | **Pass.** It uses the brand token pairs (`brand/tokens/CONTRAST.md`), and the Styler cell tints were checked to stay above 4.5:1. |
| Colour is not the only cue | **Pass** for the stickers, banners and table cells, where the word is always present. |
| Keyboard and visible focus | Mostly pass: 3 px blue focus outline. Glide data grids follow Streamlit's own keyboard model. |
| Reading level and purpose | **Pass.** Each page has a takeaway or "Question this page answers" line. |
| Table fallback for charts | **Pass.** The Metrics bar chart has a "Per-event table" expander. |
| Motion | **Pass.** It is off by default, Home only, has a Pause control, gives a still frame under reduced motion, and stops when off screen. |
| Zoom / 375 px | **Pass by screenshot.** Wide data tables scroll inside their own frame. This was not tested on a real phone. |

## Checks
- `pytest tests/test_app.py`: 11 passed. This includes a new test, `test_wallpaper_is_off_by_default_and_home_only`.
- Full suite: **312 passed, 2 skipped** (the baseline was 311 + 2 skipped; the extra one is the new test).
- `ruff check app`: clean.
- Screenshots were taken with headless Chrome over DevTools against a scratch copy of the demo DB. The click-through for 03/04 ran `console.actions` on that copy: a buyer approve REFUSED (`role`), a pharmacist approve, and an opened task. `data/demo` was not written. The server and Chrome were stopped afterwards.

## Known limits and notes
- Streamlit 1.66 rejects a font URL that names several families. The brand snippet `brand/tokens/streamlit/*.config.toml` does this, so here each font has its own URL. **Brand owner: fix `build_tokens.py`.**
- `st.html` breaks a large inline script, so the wallpaper engine is served from `app/static/` (`server.enableStaticServing = true`). Its layout CSS is merged into `app/assets/formulary.css`.
- Fonts load from Google Fonts at runtime. Offline, they fall back to Verdana/Georgia/Consolas.
- Data note: the baseline-backlog expander now reads 39 formulary items, where the old screenshot read 40. That number comes from the current demo DB, not from this restyle.

## NEXT (not built: out of scope)
- **Allocation and backorder board.** This is the #1 build-next item in `research/voc/voc-drug-shortage.md`, with 6 sources. It would show ordered vs received per wholesaler, an "expected back" date and a days-on-hand gauge, imported from shorted-line reports. The best place for it is a second panel in the Home activity rail plus its own page. A placeholder comment marks the spot in `app/Home.py`.
- Style the substitute and task `State` cells as stickers, and show a one-time checkmark when a substitute is approved (audience-data §4).
- Run a reader test of the takeaway sentence with a pharmacist (playbook rule 12). It has not been done.
