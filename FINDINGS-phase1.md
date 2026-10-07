# Pulp Explorer — Phase 1 findings

Date: 2026-10-05. Code: `pipeline/isfdb_load.py`, `pipeline/ia_metadata.py`. Raw data (not committed) lives in `data/raw/`; derived data in `data/build/`.

## Headline

**ISFDB editors have already linked 2,764 items in `pulpmagazinearchive` to specific ISFDB magazine issues**, almost all of them one-to-one. A first version can ship on those links with **no automatic matching at all**. The same links then serve as ground truth for measuring an automatic matcher that adds more issues later.

## 1. Data sources (total download cost: 4 requests to archive.org, 1 file from Google Drive)

**ISFDB**
- **Licence:** CC BY 4.0.
- **Download:** weekly MySQL backups, about 325 MB zipped and 1.5 GB of SQL, hosted on Google Drive. The live isfdb.org site sits behind a Cloudflare browser check, and its downloads page now needs a login. I found the 2025-02-01 backup link through the Wayback Machine's copy of the page. A newer backup can be fetched later by someone with a free ISFDB account; for old magazines, a 2025 snapshot loses almost nothing.
- **Load:** `pipeline/isfdb_load.py` loads the needed tables into SQLite in about 2 minutes, without a MySQL server.
- **Contents:**
  - 76,030 magazine issues
  - 882k magazine content entries, 79% with a starting page number
  - 32.9k pseudonym links, so pen names come for free
  - external identifiers, including the FictionMags Index, which could help with westerns and detective titles later

**archive.org metadata**
- `pipeline/ia_metadata.py` fetched all 28,410 items in **3 scrape-API requests, 15 seconds**.
- Fields are inconsistent:
  - `date` is present on 61.6% of items.
  - `volume` on 3%, `issue` never.
  - `subject` on 56%.
  - Titles and identifiers usually encode magazine and date in varying formats ("Astounding October 1940", `Weird_Tales_v20n02_1932-08_sas`, `1955-04_IF`).
- **Contents:** SF, fantasy and horror pulps plus a lot that ISFDB mostly doesn't cover:
  - dime novels (Beadle's about 3k, Fame and Fortune Weekly 517)
  - film magazines (Photoplay, Modern Screen)
  - Spanish *bolsilibros* (about 5k)
  - comics magazines (Creepy, Vampirella)
  - westerns and adventure titles

## 2. Existing ISFDB → archive.org links

- ISFDB has 3,872 magazine issues linked to archive.org. The links sit in the `webpages` table and in issue notes.
- Of those, **2,764 point at items in `pulpmagazinearchive`**. Only 2 items link to more than one ISFDB issue.
- Coverage is concentrated in the core SF titles:

  | magazine | linked items |
  |---|---|
  | Amazing Stories | 518 |
  | Astounding | 342 + 51 British edition |
  | Weird Tales | 214 |
  | Galaxy | 250 |
  | If | 175 |
  | Fantastic Adventures | 127 |
  | Startling Stories | 86 |
  | Thrilling Wonder Stories | 79 |
  | Planet Stories | 68 |
  | F&SF | 65 |
  | Unknown | 39 |

- **Room for automatic matching:** several large runs in the collection are barely linked. These are rough counts from titles: F&SF has about 633 items with only 65 linked, Asimov's 429 with 15, Fantastic 391 with 11, and Amazing about 716 with 518. My guess is that matching on magazine plus date could add **1.5–2.5k issues**. Phase 2 would measure that against the 2,764 known links.
- The link set is saved as `data/build/isfdb_ia_links.json`.

## 3. Printed page → BookReader page

ISFDB stores the *printed* page where each story starts. BookReader links need the *scan leaf* index `n`. There are two cases:

1. **Items with `_page_numbers.json`** (archive.org's printed-page detection). Example: `1955-04_IF`, where leaf 7 is page 6. These map directly.
2. **Fan-uploaded PDF and CBR items**, which have no page numbers (`Astounding_v61n06_1958-08…`, `Weird_Tales_v20n02_1932-08_sas`). Here, page-level OCR (`_djvu.xml`, or hOCR where it exists) lets us find each story's title near its expected page. Tested on Astounding, August 1958:

   | story | printed page | scan leaf |
   |---|---|---|
   | Hyperdemocracy | 4 | 3 |
   | We Have Fed Our Sea | 8 | 7 |
   | They've Been Working On… | 56 | 55 |
   | Cargo for Colony 6 | 66 | 66 |
   | Every Astronomer Has His Horror Story | 107 | 106 |
   | Point of Focus | 120 | 120 |

   - **The offset shifts within an issue** (−1, then 0, probably an unnumbered insert), so anchoring each story is more accurate than one offset per issue.
   - **The contents page and running headers repeat titles.** The rule: take the first title match within a small window around the printed page, skipping the contents page.
   - **When no anchor is found**, fall back to the issue's typical offset and mark the link as approximate. In the worst case, link to the issue.

**Cost:** one small file per item (`_page_numbers.json`, about 10 KB), or the OCR text when it's missing. That's about 4 MB per item for `_djvu.xml`, less when hOCR exists. For about 2.8k items, that's roughly 3–6k requests, **about 1–2 hours**, plus about 10 GB if every item needed `djvu.xml`. (In practice, keep only the extracted title hits and delete the XML.)

## 4. Proposed plan

**Phase 2: data pipeline, overnight-sized**
1. **Issues.** Start with the 2,764 ISFDB-linked items. Then an automatic matcher on magazine plus date, in a separate output tier: precision is measured against held-out known links, and a match is accepted only when it's unique and unambiguous.
2. **Contents.** For each matched issue, ISFDB supplies stories, authors (real name and pen names), and pages. Covers, interior art and letters are kept or hidden by type. V1 shows fiction, essays and editorials.
3. **Page links.** Use `_page_numbers.json` if it exists, otherwise title anchoring in page-level OCR. Each link records its method: `exact-label`, `anchored`, `offset-estimate` or `issue-only`.
4. **Evaluation.** Hand-check about 100 deep links across magazines and decades by opening the page and checking that the story starts there. Report precision by method.
5. **Output.** Static JSON: an author index, a title index, and per-issue files.

**Phase 3: frontend.** A static site with:
- search over authors, pen names and story titles
- author pages: bibliography by year, each story linking to every scanned appearance
- story pages: every appearance and reprint as a timeline
- issue pages: table of contents with deep links
- an About section with ISFDB attribution (CC BY 4.0)

**Phase 4:** extend matching beyond the SF core. That could mean the FictionMags Index for westerns and detective titles, or OCR-only table-of-contents extraction. Both are bigger projects.

## Questions for you

1. **ISFDB backup freshness.** Is the 2025-02-01 backup fine for now? Later, would you (or someone at the Archive) make a free ISFDB account so we can fetch a current one?
2. **Scope of v1.** Is starting with the ISFDB-linked items, and adding automatic matching only if its measured precision is high, the right call?
3. **Copyright posture.** Many items are fan uploads of in-copyright magazines. The tool only links to what's already public on archive.org, but it's worth a quick check with whoever owns Labs.
