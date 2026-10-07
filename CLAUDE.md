# Pulp Magazine Explorer (Wayback Machine Labs)

Public tool for Wayback Machine Labs (https://wayback-labs.sf.archive.org/), built by an Internet Archive intern. It links the Archive's scanned pulp magazines (collection `pulpmagazinearchive`, about 28.4k items) to structured bibliographic data from ISFDB (isfdb.org). A reader can search an author, pen name or story and land on the exact page of the scan where that story starts.

(The directory is called `seeds` because the project began as a seed-catalog explorer. It was dropped on 2026-10-05: extracting from OCR needed roughly 280k downloads, far too many for the time budget.)

## Goal

Pulp collectors and genre scholars care about who wrote what, under which pen names, and where and when stories were reprinted. ISFDB already records each magazine issue's contents (title, author, page number, pen names, reprints), but it doesn't link to readable scans. The Archive has the scans, but their tables of contents aren't structured. This tool joins the two. Users search a story, author or pen name and see:
- every issue where it appeared, as a timeline including reprints
- the author's real name and pen names
- a link to the BookReader page where the story starts, or to the issue when the page can't be resolved

## Design principle: the bottleneck is downloads

The whole pipeline must run overnight. Archive.org downloads are limited by the number of requests (about 1 file per second, politely), not by their size. So:
- Get item metadata with bulk search/scrape API calls (a few requests total), never one request per item.
- The structure comes from ISFDB, not OCR. Don't extract from OCR in v1.
- Per-item downloads are optional, capped at one small file per matched issue, and only if they measurably improve page links.

## How to work

Work in phases and stop for review at the end of each one.

1. **Explore and measure.**
   - Get ISFDB data: the bulk database download, after confirming the current route and the licence.
   - Scrape `pulpmagazinearchive` metadata in bulk.
   - Measure the match rate between archive.org items and ISFDB issues, by magazine and era.
   - Check how ISFDB page numbers map to BookReader pages on a sample.
   - Report the findings and a plan.
2. **Matching pipeline.**
   - Make it reproducible.
   - Match conservatively: a wrong issue link is worse than no link. Keep the match evidence (which fields matched) for every link.
   - Hand-check a sample and report precision.
   - Output is static JSON/SQLite only.
3. **Frontend.**
   - Plain static site: search with autocomplete for stories, authors and pen names; author pages; story appearance timelines; deep links.
   - An About section crediting ISFDB and explaining where the data comes from and its limits.
   - Responsive and accessible: keyboard navigation, readable on mobile.
4. **Polish and scale.** Expand beyond the first slice only after the pilot is approved.

## Constraints

- Be polite to archive.org and isfdb.org: rate-limit, cache everything locally, keep the pipeline resumable, never re-download what's already on disk. Prefer bulk exports to scraping.
- The public site never queries archive.org or ISFDB live. All data is precomputed.
- Respect ISFDB's licence and give it attribution.
- Don't commit downloaded raw data. Document how to regenerate it.
- Keep a README covering setup, the pipeline, and rebuilding the data.
- An LLM is not planned. If one becomes useful, prefer the local model (this machine is an M1 Max with 32 GB; ollama has `gemma3:27b`) and ask before spending money on an API.

## Known facts (checked 2026-10-05)

- `pulpmagazinearchive` has 28,410 items. Metadata is inconsistent:
  - Some items have a `date` (`1940-10-01T00:00:00Z`), some don't.
  - Titles vary: "Astounding October 1940", "Flash Gordon Strange Adventure Magazine v01n01 [1936-12] {JS-beb}", "Fame And Fortune Weekly (no. 662) (1918 06 07)".
  - Identifiers sometimes encode volume, issue and date (`astounding-uk-v-10-n-06-1954-06`).
  - The collection mixes SF/fantasy/horror, westerns, detective titles, dime novels, and Spanish *bolsilibros*. ISFDB mainly covers SF/fantasy/horror.
  - Watch for British and other editions ("Astounding British edition"), which are different issues from the US originals.
- ISFDB is CC BY 4.0. isfdb.org sits behind a Cloudflare browser check, and its wiki now needs a login: don't scrape it. Bulk backups are on Google Drive. The 2025-02-01 backup (Drive id `1u2v9U-mwD7vYEJ6hhraC-XQdvtkHhkZo`, found through the Wayback copy of ISFDB_Downloads) is in `data/raw/isfdb/`. `python -m pipeline.isfdb_load` loads it into `data/build/isfdb.sqlite` in about 2 minutes.
- **ISFDB already links 2,764 `pulpmagazinearchive` items to its magazine issues** (via the `webpages` table and issue notes), so these are ground truth. Details are in `FINDINGS-phase1.md`.
- Page mapping (see `pipeline/pages.py`, `FINDINGS-phase2.md`):
  - ISFDB `pubc_page` is the printed page, sometimes with a sort key (`110|110.1`).
  - Resolve it with archive.org's `_page_numbers.json` where it exists. Otherwise read printed page numbers from page-level OCR, keeping only those consistent with neighbouring pages.
  - Don't place stories by matching titles: running headers bias it 1–3 pages late. Use title matches only as a check.
  - Splash illustration pages count as the start, as in ISFDB.
  - Hand-checked precision is 79/80.
- Derived files are often named after the uploaded file, not the identifier. Match on the suffix (`_djvu.xml`, `_scandata.xml`).
- **BookReader deep links** (verified on another collection): `https://archive.org/details/<id>/page/n<N>`, where N is the 0-based index among scan leaves with `addToAccessFormats=true` in `_scandata.xml`. The first and last leaves are usually hidden color cards. Printed page labels (`_page_numbers.json`, or `/page/<label>` URLs) sometimes disagree with the leaf order. Verify them before trusting them for ISFDB page numbers.
- Download etiquette that worked:
  - At most 4 requests in flight.
  - On 429/502/503/504, halve the requests in flight and honour `Retry-After`.
  - Retry a plain 500 per file and log it.
  - Write a `.missing` marker for a 404.
  - Write files atomically.
- Automatic matching (`pipeline/match.py`):
  - Match on date and name, then verify that the issue's distinctive titles appear in the OCR.
  - Watch for traps:
    - British reprint editions with the same title and date; the page-count filter handles these.
    - Serial installments shared across neighbouring months.
    - "Next issue" previews.
    - Weeklies, which need the full date.
  - Some ISFDB scan links are wrong; settled cases go in `eval/match_adjudicated.json`.
  - Evaluate only on links made by ISFDB editors (`link_source <> 'auto'`).
