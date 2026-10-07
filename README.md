# Pulp Magazine Explorer (Wayback Machine Labs)

Links the Internet Archive's scanned pulp magazines (`pulpmagazinearchive`) to ISFDB's bibliographic records. You search an author, pen name or story and jump to the page of the scan where it starts.

Status: site built; automatic matching added (`FINDINGS-matching.md`). Phase 2 results are in `FINDINGS-phase2.md`; Phase 1 findings are in `FINDINGS-phase1.md`.

## Setup

Python 3.12+ and the `sqlite3` command line tool. No third-party Python packages are needed.

## Rebuilding the data

Raw downloads go to `data/raw/` and derived files to `data/build/`; neither is committed. Every step is cached and resumable, so re-running never re-downloads what's already on disk.

```sh
# 1. ISFDB database backup (CC BY 4.0), about 325 MB zipped.
#    Current backups are linked from https://www.isfdb.org/wiki/index.php/ISFDB_Downloads
#    (needs a free ISFDB login). This build uses the 2025-02-01 backup:
mkdir -p data/raw/isfdb
curl -L -o data/raw/isfdb/isfdb-2025-02-01.zip \
  "https://drive.usercontent.google.com/download?id=1u2v9U-mwD7vYEJ6hhraC-XQdvtkHhkZo&export=download&confirm=t"
unzip -p data/raw/isfdb/isfdb-2025-02-01.zip > data/raw/isfdb/isfdb-2025-02-01.sql

# 2. Load the tables we need into SQLite (about 2 minutes)        -> data/build/isfdb.sqlite
python3 -m pipeline.isfdb_load data/raw/isfdb/isfdb-2025-02-01.sql data/build/isfdb.sqlite

# 3. archive.org metadata for the collection (3 requests)         -> data/raw/ia/pulpmagazinearchive.jsonl
python3 -m pipeline.ia_metadata pulpmagazinearchive data/raw/ia/pulpmagazinearchive.jsonl

# 4. Join ISFDB-linked items to issues, contents, authors          -> data/build/pulp.sqlite
python3 -m pipeline.issues

# 5. Per-item page data (about 2 hours, about 550 MB)              -> data/raw/ia/items/<id>/
python3 -m pipeline.fetch

# 6. Printed page -> BookReader page for every story               -> links table in pulp.sqlite
python3 -m pipeline.pages

# 7. Hand-check sample and precision report
python3 -m pipeline.evaluate sample 80
python3 -m pipeline.evaluate report eval/sample.csv

# 8. Automatic matching of scans ISFDB hasn't linked (optional; see below)
python3 -m pipeline.match candidates     # -> data/build/match_candidates.json
python3 -m pipeline.match eval           # precision/recall against ISFDB's own links
python3 -m pipeline.match ids            # -> data/build/match_fetch_ids.txt
python3 -m pipeline.fetch --ids data/build/match_fetch_ids.txt   # about 7.5k items, a few hours
python3 -m pipeline.match verify         # -> data/build/matches.json
python3 -m pipeline.issues && python3 -m pipeline.pages   # re-run so matches get page links

# 9. Static JSON for the site (a few seconds)                     -> site/data/
python3 -m pipeline.export
```

## The site

`site/` is plain HTML, CSS and JavaScript: no build step, no frameworks, and no requests to anything except its own `data/` folder. Links go out to archive.org and ISFDB only when a reader clicks them.

```sh
python3 -m http.server 8765 --directory site      # then open http://127.0.0.1:8765/
docker build -t pulp-explorer . && docker run -p 8080:80 pulp-explorer   # same thing, served by nginx
```

URLs are hash routes, so any page can be linked:
- `#/author/<isfdb author id>`
- `#/author/<id>/w<work id>`, which jumps to one story
- `#/issue/<isfdb pub id>`
- `#/magazine/<name>`
- `#/magazines`
- `#/about`

`site/data/` is generated, so it's gitignored. It's about 65 MB in about 12k small JSON files; the search index is 1.9 MB, or about 0.5 MB gzipped, and loads when the search box is first used.

## Pipeline

| module | does |
|---|---|
| `pipeline/isfdb_load.py` | parses the MySQL dump directly (no MySQL server) and loads the needed tables into SQLite |
| `pipeline/ia_metadata.py` | bulk collection metadata via archive.org's scrape API |
| `pipeline/issues.py` | archive.org items that ISFDB editors linked to magazine issues (via the `webpages` table and issue notes), with their contents, author credits and pen names. Retitled or pen-name variants are grouped under one `work_id`. |
| `pipeline/fetch.py` | per item: file list, `_scandata.xml`, `_page_numbers.json` when it exists, and page-level OCR (hOCR, or `_djvu.xml` reduced to page text) |
| `pipeline/pages.py` | resolves ISFDB's printed page to a BookReader `n`, using archive.org's page labels, then page numbers read from the OCR, then one-sided extrapolation; otherwise falls back to an issue-level link. Each link records its method. |
| `pipeline/evaluate.py` | stratified hand-check sample and precision report (rubric in the module docstring) |
| `pipeline/match.py` | matches unlinked scans to ISFDB issues. Candidates come from cover date (month, season, day, volume/number) and magazine name, with name aliases learned from ISFDB's existing links. A candidate is confirmed only if the issue's distinctive story titles are found in the scan's OCR, and a page-count check rules out partial reprint editions. Disagreements with ISFDB that were checked against the scans are recorded in `eval/match_adjudicated.json`. |
| `pipeline/export.py` | static JSON for the site: search index, author pages grouped by work, issue tables of contents, magazines. Pen names appear only if printed on a byline in these scans; placeholder credits ("uncredited", "The Editor") get no author page. |

## Downloading politely

`pipeline/fetch.py` keeps at most 4 requests in flight: it starts at 2 and adds one after 50 successes in a row.
- **429/502/503/504 and connection errors:** halve the limit and pause every worker, honouring `Retry-After`.
- **A plain 500:** retried for that file only, then logged to `data/raw/ia/fetch_failures.log`.
- **A 404:** leaves a `.missing` marker so the file isn't requested again.
- **Writes:** atomic.

## Data credits

Bibliographic data comes from the [Internet Speculative Fiction Database](https://www.isfdb.org/) and is used under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Scans come from the Internet Archive's `pulpmagazinearchive` collection.

Fonts: [Anton](https://fonts.google.com/specimen/Anton) by Vernon Adams and [Alfa Slab One](https://fonts.google.com/specimen/Alfa+Slab+One) by JM Solé, both under the [SIL Open Font License 1.1](https://openfontlicense.org/). They're self-hosted from `site/fonts/` (Latin subset), so the site makes no outside requests.
