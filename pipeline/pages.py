"""Resolve each story's printed start page to a BookReader page index `n`.

    python -m pipeline.pages

Reads data/build/pulp.sqlite and data/raw/ia/items/<id>/; writes table `links` into
data/build/pulp.sqlite:
  links(ia_id, pub_id, title_id, page, n, method, label_n, folio_n, checked)

Methods, in the order they're trusted:
  label   archive.org's printed-page detection (_page_numbers.json)
  folio   our own printed-page detection: page numbers read from the top/bottom of each
          OCR page, kept only when consistent with neighbouring pages; a story's page
          must be printed on its leaf, or bracketed by numbered pages that agree on the
          leaf offset
  folio-ext  one-sided version of folio: no numbered page on one side within BRACKET, but
          the EXT_AGREE nearest numbered pages on the other side (within 2*BRACKET) all
          agree on the offset. Measured separately.
  Both folio methods reject a position that isn't strictly between the scan positions of
  the nearest numbered pages below and above the printed page (that page isn't in the scan),
  or any position between two numbered pages whose scan distance doesn't match their
  printed distance (pages missing or inserted there, so the placement is ambiguous).
  issue   nothing resolved; link to the issue's first page
`checked` is 1 when the story's title appears in the OCR of the resolved leaf or the
next CHECK_AHEAD leaves (pulps often open with a full-page illustration and print the
title as a running header), 0 when it doesn't, NULL when the title is too short to test.
Title anchoring alone is not used to place stories: running headers bias it late.
"""
import gzip
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ITEMS = Path("data/raw/ia/items")
DB = Path("data/build/pulp.sqlite")
TEXT_TYPES = ("SHORTFICTION", "SERIAL", "NOVEL", "ESSAY", "POEM", "EDITOR", "REVIEW", "INTERVIEW")
CHECK_AHEAD = 3      # leaves after the start page where the title may first be printed
FOLIO_EDGE = 4       # tokens at the start/end of a page searched for a page number
FOLIO_NEAR = 4       # a folio is kept only if a page this close agrees on the offset
BRACKET = 8          # max printed-page distance to the numbered pages bracketing a story
EXT_AGREE = 3        # numbered pages that must agree for one-sided extrapolation
MIN_TITLE = 6        # shorter normalized titles are too common to check

SUFFIX = re.compile(r"\s*\((?:part \d+ of \d+|[^()]*\b(?:18|19|20)\d\d)\)\s*$", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def anchor_key(title):
    """Normalized title for matching, minus ISFDB's '(Part 1 of 2)' / '(Astounding, May 1958)'."""
    for _ in range(2):
        title = SUFFIX.sub("", title)
    return norm(title)


def leaves(d):
    """Scandata leaf list as [(leafNum, visible_in_bookreader)], or None if absent."""
    p = d / "scandata.xml"
    if not p.exists():
        return None
    x = p.read_text(errors="replace")
    out = []
    for m in re.finditer(r'<page leafNum="(\d+)"(.*?)</page>', x, re.S):
        access = re.search(r"<addToAccessFormats>(\w+)</addToAccessFormats>", m.group(2))
        out.append((int(m.group(1)), not access or access.group(1) == "true"))
    return out or None


def label_leaves(d):
    """Printed page label -> leafNum, from _page_numbers.json (first occurrence wins)."""
    p = d / "page_numbers.json"
    if not p.exists():
        return {}
    try:
        pages = json.loads(p.read_text()).get("pages", [])
    except json.JSONDecodeError:
        return {}
    out = {}
    for pg in pages:
        label = str(pg.get("pageNumber") or "").strip()
        if label and label not in out:
            out[label] = pg["leafNum"]
    return out


def printed_page(page):
    """ISFDB page field -> int. '110|110.1' is page 110 plus a sort key; 'fep', 'bc', roman -> None."""
    head = page.split("|")[0].strip()
    return int(head) if head.isdigit() else None


def detect_folios(pages):
    """position -> printed page number, for pages whose number agrees with a neighbour."""
    cands = []
    for pos, text in enumerate(pages):
        toks = text.split()
        edge = toks[:FOLIO_EDGE] + toks[-FOLIO_EDGE:]
        cands.append({pos - int(t) for t in edge if t.isdigit() and 0 < int(t) < 1000})
    out = {}
    for pos, offsets in enumerate(cands):
        for o in offsets:
            if any(o in cands[q] for q in range(max(0, pos - FOLIO_NEAR), min(len(pages), pos + FOLIO_NEAR + 1))
                   if q != pos):
                out[pos] = pos - o
                break
    return out


def resolve_item(ia_id, stories):
    """stories: [(pub_id, title_id, page, ttype, title)] for one item."""
    d = ITEMS / ia_id
    leaf_list = leaves(d)
    text = None
    if (d / "pagetext.json.gz").exists():
        text = json.loads(gzip.decompress((d / "pagetext.json.gz").read_bytes()))
    pages = text["pages"] if text else []

    # BookReader n for each text page position. hOCR has one page per scanned leaf;
    # djvu.xml may have only the visible ones, so decide by count.
    if leaf_list:
        visible = [i for i, (_, vis) in enumerate(leaf_list) if vis]
        n_of_pos = {pos: n for n, pos in enumerate(visible)}
        pos_of_leafnum = {num: pos for pos, (num, _) in enumerate(leaf_list)}
        if len(pages) == len(leaf_list):
            page_n = [n_of_pos.get(i) for i in range(len(pages))]
        elif len(pages) == len(visible):
            page_n = list(range(len(pages)))
        else:
            page_n = [None] * len(pages)
    else:
        n_of_pos, pos_of_leafnum = None, {}
        page_n = list(range(len(pages)))
    norm_pages = [norm(p) for p in pages]
    labels = label_leaves(d)
    folios = detect_folios(pages)
    pos_of_folio = {}
    for pos, num in folios.items():
        pos_of_folio.setdefault(num, pos)

    def label_n(p):
        num = labels.get(str(p))
        if num is None or n_of_pos is None:
            return None
        return n_of_pos.get(pos_of_leafnum.get(num))

    def folio_n(p):
        """-> (n, 'folio' | 'folio-ext') or (None, None)."""
        if p in pos_of_folio:
            return page_n[pos_of_folio[p]], "folio"
        lower = max((num for num in pos_of_folio if num < p), default=None)
        upper = min((num for num in pos_of_folio if num > p), default=None)

        def placed(pos, method):
            # The page must fall strictly between the numbered pages around it; otherwise
            # it isn't in this scan (e.g. pages removed from a '_modified' item).
            if not 0 <= pos < len(page_n) or page_n[pos] is None:
                return None, None
            if (lower is not None and pos <= pos_of_folio[lower]) or \
                    (upper is not None and pos >= pos_of_folio[upper]):
                return None, None
            # Between two numbered pages the scan must hold exactly the pages the numbers
            # imply; more or fewer means pages are missing or inserted, so it's ambiguous.
            if lower is not None and upper is not None and \
                    pos_of_folio[upper] - pos_of_folio[lower] != upper - lower:
                return None, None
            return page_n[pos], method

        if lower is None or upper is None or p - lower > BRACKET or upper - p > BRACKET:
            side = sorted(((abs(num - p), pos - num) for num, pos in pos_of_folio.items()
                           if abs(num - p) <= 2 * BRACKET))[:EXT_AGREE]
            offsets = {o for _, o in side}
            if len(side) == EXT_AGREE and len(offsets) == 1:
                return placed(p + offsets.pop(), "folio-ext")
            return None, None
        lp, up = pos_of_folio[lower], pos_of_folio[upper]
        if lp - lower != up - upper:
            return None, None
        return placed(p + (lp - lower), "folio")

    def check(n, title):
        k = anchor_key(title)
        if len(k) < MIN_TITLE or n is None:
            return None
        window = [i for i, pn in enumerate(page_n) if pn is not None and n <= pn <= n + CHECK_AHEAD]
        return int(any(k in norm_pages[i] for i in window))

    rows = []
    for pub_id, title_id, page, ttype, title in stories:
        if ttype not in TEXT_TYPES:
            continue
        p = printed_page(page)
        ln = label_n(p) if p is not None else None
        fn, fmethod = folio_n(p) if p is not None else (None, None)
        if ln is not None:
            n, method = ln, "label"
        elif fn is not None:
            n, method = fn, fmethod
        else:
            n, method = 0, "issue"
        rows.append((ia_id, pub_id, title_id, page, n, method, ln, fn,
                     check(n, title) if method != "issue" else None))
    return rows


def main():
    db = sqlite3.connect(DB)
    stories = {}
    for row in db.execute("""SELECT i.ia_id, c.pub_id, c.title_id, COALESCE(c.page, ''), c.ttype, c.title
                             FROM items i JOIN contents c USING(pub_id)"""):
        stories.setdefault(row[0], []).append(row[1:])
    fetched = {p.name for p in ITEMS.iterdir() if (p / "files.json").exists()}
    rows = []
    for ia_id in sorted(stories):
        if ia_id in fetched:
            rows += resolve_item(ia_id, stories[ia_id])
    db.execute("DROP TABLE IF EXISTS links")
    db.execute("CREATE TABLE links(ia_id, pub_id, title_id, page, n, method, label_n, folio_n, checked)")
    db.executemany("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    print(f"{len({r[0] for r in rows})} items, {len(rows)} links")
    for method, count in db.execute("SELECT method, COUNT(*) FROM links GROUP BY method ORDER BY 2 DESC"):
        print(f"  {method:9} {count:6}  {count / len(rows):.0%}")
    for method, chk, tot in db.execute("""SELECT method, SUM(checked), COUNT(checked) FROM links
                                           WHERE method <> 'issue' GROUP BY method"""):
        print(f"  {method}: title found at/after the page in {chk}/{tot} ({chk / max(tot, 1):.0%})")
    both, same = db.execute("""SELECT COUNT(*), SUM(label_n = folio_n) FROM links
                               WHERE label_n IS NOT NULL AND folio_n IS NOT NULL""").fetchone()
    print(f"  label vs folio agree: {same}/{both}")

if __name__ == "__main__":
    main()
