# Pulp Explorer — Phase 2 results (data pipeline)

Date: 2026-10-05. Code: `pipeline/`. Hand-check: `eval/sample.csv`. How to rebuild: `README.md`.

## Summary

- **Scope.** There are 2,765 archive.org items that ISFDB editors linked to 2,753 magazine issues. Those issues contain **52,448 stories, essays, poems, reviews and editorials**: 48k distinct works by 16k authors, with 5.4k pen-name links.
- **Downloads.** About 2 hours with zero failed requests, about 550 MB on disk. **2,755 of 2,765 items have page-level OCR.** The other 10 have no OCR on archive.org, or several scans in one item.
- **Page links:**

  | method | links | share | how |
  |---|---|---|---|
  | `folio` | 32,969 | 63% | printed page numbers we read from the OCR |
  | `label` | 10,534 | 20% | archive.org's own printed-page detection |
  | `folio-ext` | 2,612 | 5% | one-sided version of `folio` (3 nearest numbered pages agree) |
  | `issue` | 6,333 | 12% | no page resolved; link goes to the issue |

- **Precision**, from a hand-check of 80 links (40 `label`, 40 `folio`). Each check looked at the page images for n−1, n and n+1. Four helper agents judged the images, and I re-viewed some of their verdicts myself.

  | | links checked | exact start page |
  |---|---|---|
  | all | 80 | **79 (99%)**, 95% CI about 93–100% |
  | label | 40 | 39 |
  | folio | 40 | 40 |

  The one miss is an Interzone issue with a bound-in subscription insert, which threw off archive.org's page labels.
- **Two independent methods agree.** Where `label` and `folio` both resolve a story, they point at the same page for **9,665 of 9,708** stories (99.6%). `folio-ext` agrees with `label` for 353 of 359 (98.3%). `folio-ext` hasn't been hand-checked yet.

## What was learned

- **Splash pages.** Pulps often open a story with a full-page illustration and print the title on the next page. ISFDB counts the splash page as the start, which is also the right link because the reader sees the opening art first.
- **Matching titles in the OCR is not a good placement method.** Running headers repeat the title every page or two, while display-type titles on the real first page often OCR badly ("LONCON n", "DITORIAL"). An earlier version that relied on titles placed stories 1–3 pages late. Titles are now only a weak check (`links.checked`), not used for placement.
- **Printed page numbers in the OCR are the key.** Reading the numbers at the top or bottom of each page, and keeping only those consistent with neighbouring pages, gives the offset between printed page and scan, even when it shifts mid-issue because of unnumbered inserts or plates.
- **Data quirks handled:**
  - ISFDB page fields like `110|110.1` (page plus a sort key).
  - Derived files named after the uploaded PDF rather than the item.
  - `djvu.xml`-only items.
  - Items with removed pages (`_modified`).

## What's not resolved (the 12% `issue` links)

- **ISFDB has no page** for 132+ entries, mostly departments and editorials.
- **Unnumbered front matter** (pages 1–6): contents and editorials, about 1.2k.
- **No consistent numbered pages nearby**, mostly long ad sections and badly OCR'd page numbers. Of these, about 600 have numbered pages on both sides that disagree about the offset.
- **Edge cases:**
  - Two-page-spread scans (some British editions), where one image holds two printed pages. These need a separate mode.
  - Page numbers buried in footers in some 1990s magazines.

## Decisions for you

1. **Publish `label`, `folio` and `folio-ext` links as page links, and `issue` links as "open the issue".** My recommendation, since precision is at the level you asked for. `folio-ext` is the least tested; I can hand-check 20–30 of them before publishing.
2. **Spot-check.** The verdicts in `eval/sample.csv` came from AI checkers looking at page images. Each row has a `link`, so a 10-row human spot-check would make the precision figure more trustworthy.
3. **Next steps**, either order:
   - (a) the static export (author, story and issue JSON) plus the Phase 3 frontend, or
   - (b) the automatic matcher for the roughly 1.5–2.5k unlinked SF issues (F&SF, Asimov's, Fantastic…), measured against the 2,765 known links.

## Update 2026-10-06: `folio-ext` check and a gap rule

- **Hand-check.** 30 random `folio-ext` links were checked against page images (`eval/sample_ext.csv`); 29 were correct. The miss was in a `_modified` scan that is missing printed pages 52–70: extrapolating from the numbered pages after the gap placed page 69 on a scan page that shows page 51.
- **New rule in `pipeline/pages.py`.** A resolved scan page must fall strictly between the nearest numbered pages below and above it. Between those two pages, the scan must hold exactly as many pages as their printed numbers imply; more or fewer means pages are missing or inserted, so the placement is ambiguous and the story falls back to an issue link.
- **Effect:**
  - About 110 links moved to issue-only. 75 of them were `folio-ext`, including 3 that had been confirmed correct; the rule is deliberately conservative.
  - All 80 links in the original sample are unchanged.
  - The 26 remaining `folio-ext` checks are all correct.
- **Overall:** 108 of 110 hand-checked links were correct, and the cause of one of the two misses is now handled.
