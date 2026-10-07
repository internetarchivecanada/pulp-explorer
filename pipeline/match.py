"""Match archive.org pulp items that ISFDB hasn't linked to ISFDB magazine issues.

    python -m pipeline.match candidates   -> data/build/match_candidates.json (all items)
    python -m pipeline.match eval         precision/recall on the ISFDB-linked items (2-fold)
    python -m pipeline.match ids          -> data/build/match_fetch_ids.txt (unlinked items to fetch)
    python -m pipeline.match verify       -> data/build/matches.json (accepted matches, with evidence)
                                             data/build/unmatched.json (the rest, with candidate magazines)

1. Candidates (generous, for recall). Parse each item's cover date from its identifier,
   title or `date` field: a month, a season, or a bare year. An ISFDB magazine issue is a
   candidate if its date is compatible (month +/-1, season months, or same year when the
   item has only a year) AND its magazine name fits the item: either every word of the
   ISFDB magazine name appears in the item's identifier/title, or the item's name key
   (e.g. "astounding" from Astounding_v17n06_1936-08) has been linked to that magazine
   series by ISFDB editors before (aliases learned from the known links).
2. Verification (for precision). Candidates whose printed page count is clearly smaller
   than the scan's are dropped (a 164-page scan isn't the 64-page British reprint edition of
   the same month). Each remaining candidate issue's distinctive contents (story and
   article titles long enough to be specific, and not repeated across issues, like
   "Letters") are looked for in the scan's OCR text. A match is accepted only when the best
   candidate finds at least MIN_FOUND_SHARE of its testable titles (and at least
   MIN_TESTABLE of them exist) and beats the runner-up by MARGIN.
"""
import gzip
import json
import re
import sqlite3
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.pages import anchor_key, norm

IA_META = Path("data/raw/ia/pulpmagazinearchive.jsonl")
ITEMS = Path("data/raw/ia/items")
ISFDB = Path("data/build/isfdb.sqlite")
PULP = Path("data/build/pulp.sqlite")
CANDIDATES = Path("data/build/match_candidates.json")
MATCHES = Path("data/build/matches.json")
UNMATCHED = Path("data/build/unmatched.json")

MIN_KEY = 10           # normalized title length to count as testable
MAX_TITLE_PUBS = 2     # titles printed in more magazine issues than this aren't distinctive
MIN_TESTABLE = 3
MIN_FOUND_SHARE = 0.5
MARGIN = 0.25

MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
SEASONS = {"winter": {12, 1, 2}, "spring": {3, 4, 5}, "summer": {6, 7, 8}, "fall": {9, 10, 11}, "autumn": {9, 10, 11}}
STOP = {"the", "and", "of", "a", "magazine", "stories", "story", "science", "fiction", "vol", "volume", "no",
        "edition", "issue", "scan", "pdf", "modified", "fantasy", "tales", "monthly", "quarterly", "annual"}
YEAR = r"(1[89]\d\d|20[0-2]\d)"


def words(s):
    return re.findall(r"[a-z]+", s.lower().replace("_", " "))


def parse_date(text, date_field):
    """-> (year, set of possible months or None for unknown)."""
    t = text.lower().replace("_", " ")
    m = re.search(YEAR + r"[- .](0[1-9]|1[0-2])\b", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return y, {mo}
    m = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b[\s,.-]*(?:\d{1,2},?\s*)?" + YEAR, t)
    if m:
        return int(m.group(2)), {MONTHS[m.group(1)]}
    m = re.search(YEAR + r"[\s_-]?(wi|win|sp|spr|su|sum|fa|fal)(?![a-z])", t)
    if m:
        return int(m.group(1)), set(SEASONS[{"wi": "winter", "sp": "spring", "su": "summer", "fa": "fall"}[m.group(2)[:2]]])
    m = re.search(r"\b(" + "|".join(SEASONS) + r")\b[\s,-]*" + YEAR, t) or \
        re.search(YEAR + r"[\s,-]*\b(" + "|".join(SEASONS) + r")\b", t)
    if m:
        a, b = m.group(1), m.group(2)
        season, year = (a, b) if a in SEASONS else (b, a)
        return int(year), set(SEASONS[season])
    d = str(date_field or "")
    if re.match(r"\d{4}-\d\d", d) and d[5:7] != "00" and d[5:7] != "01":
        return int(d[:4]), {int(d[5:7])}     # '-01-01' is often a year-only placeholder
    m = re.search(YEAR, t) or (re.match(YEAR, d) if d else None)
    if m:
        return int(m.group(1)), None
    return None, None


def parse_day(text):
    m = re.search(YEAR + r"[- .](0[1-9]|1[0-2])[- .](0[1-9]|[12]\d|3[01])\b", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_volnum(text):
    """'v01n02', 'V06N09', 'Vol. 1, No. 2', 'Vol 1 No 2' -> (1, 2)."""
    m = re.search(r"\bv(?:ol)?\.?\s*0*(\d{1,3})[\s,._-]*n(?:o|umber)?\.?\s*0*(\d{1,3})(?!\d)", text, re.I) or \
        re.search(r"(?<![a-z])v0*(\d{1,3})n0*(\d{1,3})(?!\d)", text, re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def name_key(identifier):
    """Leading words of the identifier, before any digits: 'Amazing_Stories_Quarterly_v02...' ->
    'amazing stories quarterly', 'AdventureV042N02...' -> 'adventure', 'PlanetStoriesV06N09...' ->
    'planet stories'. Identifiers that start with a date use their words instead. May be ''."""
    lead = re.match(r"[A-Za-z _.\-]*", identifier).group(0)
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", lead)
    toks = [p.lower() for p in parts if len(p) > 1]
    if not toks:
        toks = [t for t in re.findall(r"[a-z]+", identifier.lower())
                if len(t) > 1 and t not in MONTHS and t not in SEASONS][:2]
    return " ".join(toks)


def load_isfdb_issues(db):
    """ISFDB magazine issues: pub_id -> {title, year, months, series, name_words}."""
    series = dict(db.execute("""
        SELECT pc.pub_id, MIN(s.series_title) FROM pub_content pc JOIN titles t ON t.title_id = pc.title_id
        JOIN series s ON s.series_id = t.series_id JOIN pubs p ON p.pub_id = pc.pub_id
        WHERE t.title_ttype = 'EDITOR' AND p.pub_ctype = 'MAGAZINE' GROUP BY pc.pub_id"""))
    issues = {}
    for pub_id, title, date in db.execute("SELECT pub_id, pub_title, pub_year FROM pubs WHERE pub_ctype = 'MAGAZINE'"):
        y = int(date[:4]) if date and date[:4].isdigit() and date[:4] != "0000" else None
        mo = int(date[5:7]) if date and date[5:7].isdigit() and date[5:7] != "00" else None
        months = {mo} if mo else None
        tl = title.lower()
        for s, ms in SEASONS.items():
            if re.search(rf"\b{s}\b", tl):
                months = set(ms)
        mag = title.split(",")[0]
        day = int(date[8:10]) if date and date[8:10].isdigit() and date[8:10] != "00" else None
        issues[pub_id] = {"title": title, "year": y, "months": months, "series": series.get(pub_id),
                          "name_words": set(words(mag)) - STOP, "day": day, "volnum": parse_volnum(title)}
    return issues


def compatible(item_months, issue_months, item_has_month):
    if item_months is None or issue_months is None:
        return not item_has_month or issue_months is None or item_months is None
    widened = {((m - 2) % 12) + 1 for m in item_months} | item_months | {(m % 12) + 1 for m in item_months}
    return bool(widened & issue_months)


def candidates_for(item, issues_by_year, aliases):
    text = f"{item['identifier']} {item.get('title', '')}"
    year, months = parse_date(text, item.get("date"))
    if not year:
        return []
    iw = set(words(text))
    day = parse_day(text.replace("_", " "))
    volnum = parse_volnum(text.replace("_", " "))
    key = name_key(item["identifier"])
    alias_series = aliases.get(key, set()) if key else set()
    out = []
    for yy in (year - 1, year, year + 1) if months and (months & {12, 1}) else (year,):
        for pub_id, iss in issues_by_year.get(yy, []):
            if yy == year - 1 and not (iss["months"] and 12 in iss["months"] and months & {1}):
                continue
            if yy == year + 1 and not (iss["months"] and 1 in iss["months"] and months & {12}):
                continue
            if not compatible(months, iss["months"], months is not None):
                continue
            by_name = bool(iss["name_words"]) and iss["name_words"] <= iw
            by_alias = iss["series"] in alias_series
            if not (by_name or by_alias):
                continue
            if day and iss["day"] and (yy, iss["day"]) != (day[0], day[2]):
                continue
            if volnum and iss["volnum"] and volnum != iss["volnum"]:
                continue
            exact = months is None or iss["months"] is None or bool(months & iss["months"])
            out.append((pub_id, exact))
    # Neighbouring months are only for items whose own month has no candidate at all.
    if any(e for _, e in out):
        out = [(p, e) for p, e in out if e]
    return [p for p, _ in out]


def learn_aliases(items, links):
    """name key -> magazine series, from ISFDB's existing item -> issue links."""
    db = sqlite3.connect(ISFDB)
    series = dict(db.execute("""
        SELECT pc.pub_id, MIN(s.series_title) FROM pub_content pc JOIN titles t ON t.title_id = pc.title_id
        JOIN series s ON s.series_id = t.series_id WHERE t.title_ttype = 'EDITOR' GROUP BY pc.pub_id"""))
    out = defaultdict(set)
    for ia_id, pub_id in links:
        key = name_key(ia_id)
        if key and series.get(pub_id):
            out[key].add(series[pub_id])
    return out


def known_links():
    """Links made by ISFDB editors (never the matcher's own output)."""
    return [tuple(r) for r in sqlite3.connect(PULP).execute(
        "SELECT ia_id, pub_id FROM items WHERE link_source <> 'auto'")]


def load_items():
    return {d["identifier"]: d for d in map(json.loads, IA_META.open())}


def build_candidates():
    items = load_items()
    issues = load_isfdb_issues(sqlite3.connect(ISFDB))
    by_year = defaultdict(list)
    for pub_id, iss in issues.items():
        if iss["year"]:
            by_year[iss["year"]].append((pub_id, iss))
    links = known_links()
    linked_ids = {i for i, _ in links}
    # Two folds over the known links, so a linked item's own link never teaches its alias.
    fold = {i: zlib.crc32(i.encode()) & 1 for i in linked_ids}
    folds = [learn_aliases(items, [(i, p) for i, p in links if fold[i] != k]) for k in (0, 1)]
    full = learn_aliases(items, links)
    out = {}
    for ia_id, item in items.items():
        aliases = folds[fold[ia_id]] if ia_id in linked_ids else full
        out[ia_id] = candidates_for(item, by_year, aliases)
    CANDIDATES.write_text(json.dumps(out))
    truth = dict(links)
    n_true = sum(1 for i in linked_ids if truth[i] in out[i])
    with_c = Counter("none" if not c else "one" if len(c) == 1 else "several" for i, c in out.items() if i not in linked_ids)
    print(f"known links: true issue among candidates for {n_true}/{len(linked_ids)} ({n_true / len(linked_ids):.1%})")
    print(f"unlinked items: {dict(with_c)}")


# ---------- Verification ----------

def distinctive_titles(db, pub_ids):
    """pub_id -> list of (title_id, key) worth looking for in a scan."""
    db.execute("CREATE TEMP TABLE IF NOT EXISTS want_v(pub_id INTEGER PRIMARY KEY)")
    db.execute("DELETE FROM want_v")
    db.executemany("INSERT OR IGNORE INTO want_v VALUES (?)", [(p,) for p in pub_ids])
    rows = db.execute("""SELECT pc.pub_id, t.title_id, t.title_title FROM pub_content pc JOIN want_v USING(pub_id)
                         JOIN titles t ON t.title_id = pc.title_id
                         WHERE t.title_ttype IN ('SHORTFICTION','NOVEL','ESSAY','POEM')""").fetchall()
    keys = {(p, t): anchor_key(title) for p, t, title in rows}
    # How many magazine issues print each normalized title (departments like "Letters" repeat).
    spread = Counter()
    for k in set(keys.values()):
        spread[k] = 0
    for (p, _), k in keys.items():
        spread[k] += 1
    # A title in many *candidate* issues still might be unique ISFDB-wide; use a global count.
    global_counts = title_pub_counts(db, {k for k in keys.values() if len(k) >= MIN_KEY})
    out = defaultdict(list)
    for (p, t), k in keys.items():
        if len(k) >= MIN_KEY and global_counts.get(k, 0) <= MAX_TITLE_PUBS:
            out[p].append((t, k))
    return out


_title_counts = None


def title_pub_counts(db, keys):
    global _title_counts
    if _title_counts is None:
        _title_counts = Counter()
        for title, n in db.execute("""SELECT t.title_title, COUNT(DISTINCT pc.pub_id) FROM titles t
                                      JOIN pub_content pc USING(title_id) JOIN pubs p ON p.pub_id = pc.pub_id
                                      WHERE p.pub_ctype = 'MAGAZINE'
                                        AND t.title_ttype IN ('SHORTFICTION','SERIAL','NOVEL','ESSAY','POEM')
                                      GROUP BY t.title_title"""):
            _title_counts[anchor_key(title)] += n
    return _title_counts


def parse_pages(field):
    """ISFDB page count: '164', 'vi+160', '[4]+128+[4]' -> sum of the arabic numbers, or None."""
    nums = [int(x) for x in re.findall(r"\d+", field or "")]
    return sum(nums) or None


def scan_pages(ia_id):
    p = ITEMS / ia_id / "pagetext.json.gz"
    return len(json.loads(gzip.decompress(p.read_bytes()))["pages"]) if p.exists() else None


def page_fits(n_scan, n_pub):
    """A scan can't hold much more than the printed issue (covers and an insert or two at most).
    Fewer is fine: scans with pages removed are common."""
    if not n_scan or not n_pub:
        return True
    return n_scan <= n_pub * 1.15 + 12


def scan_text(ia_id):
    p = ITEMS / ia_id / "pagetext.json.gz"
    if not p.exists():
        return None
    return norm(" ".join(json.loads(gzip.decompress(p.read_bytes()))["pages"]))


def verify_items(item_ids, cands):
    db = sqlite3.connect(ISFDB)
    pubs = {p for i in item_ids for p in cands.get(i, [])}
    titles = distinctive_titles(db, pubs)
    issues = load_isfdb_issues(db)
    issue_dates = {p: issues[p]["months"] for p in pubs if p in issues}
    pub_pages = {p: parse_pages(pp) for p, pp in db.execute(
        f"SELECT pub_id, pub_pages FROM pubs WHERE pub_id IN ({','.join(map(str, pubs)) or 0})")}
    items = load_items()
    item_months = {i: parse_date(f"{i} {items[i].get('title', '')}", items[i].get("date"))[1] for i in item_ids}
    results = {}
    for ia_id in item_ids:
        cs = cands.get(ia_id, [])
        if not cs:
            results[ia_id] = {"decision": "no-candidate"}
            continue
        text = scan_text(ia_id)
        if text is None:
            results[ia_id] = {"decision": "no-text"}
            continue
        n_pages = scan_pages(ia_id)
        fits = [p for p in cs if page_fits(n_pages, pub_pages.get(p))]
        if not fits:
            results[ia_id] = {"decision": "reject", "reason": "page count"}
            continue
        scored = []
        for p in fits:
            ts = titles.get(p, [])
            found = frozenset(k for t, k in ts if k in text)
            iss = issue_dates.get(p)
            exact = bool(item_months.get(ia_id) and iss and iss & item_months[ia_id])
            scored.append({"pub_id": p, "share": len(found) / len(ts) if ts else 0.0, "testable": len(ts),
                           "found": found, "exact": exact})
        scored.sort(key=lambda c: (len(c["found"]), c["share"]), reverse=True)
        best = scored[0]
        if len(scored) > 1:
            # Among candidates that found exactly the same stories (e.g. a US issue and its
            # partial UK reprint, scanned as the UK edition), the smaller edition explains it best.
            same = [c for c in scored if c["found"] == best["found"]]
            best = max(same, key=lambda c: c["share"])
        others = [c for c in scored if c is not best]
        runner = max(others, key=lambda c: (c["share"], len(c["found"]))) if others else None
        ok = best["testable"] >= MIN_TESTABLE and best["share"] >= MIN_FOUND_SHARE
        if ok and runner:
            if runner["found"] < best["found"]:
                pass  # runner's finds are a strict subset: a partial reprint edition
            elif runner["found"] == best["found"]:
                ok = best["share"] - runner["share"] >= MARGIN
            else:
                ok = len(best["found"]) > len(runner["found"]) and best["share"] - runner["share"] >= MARGIN
        # A candidate with the item's own printed date that also passes the content check,
        # but loses, means the scan carries another issue's titles (previews): too ambiguous.
        if ok and not best["exact"] and any(c["exact"] and c["share"] >= MIN_FOUND_SHARE for c in others):
            ok = False
        results[ia_id] = {"decision": "match" if ok else "reject", "pub_id": best["pub_id"], "exact_date": best["exact"],
                          "share": round(best["share"], 3), "found": len(best["found"]),
                          "testable": best["testable"], "runner_up": round(runner["share"], 3) if runner else 0.0,
                          "n_candidates": len(cs)}
    return results


def evaluate():
    cands = json.loads(CANDIDATES.read_text())
    links = dict(known_links())
    res = verify_items(sorted(links), cands)
    c = Counter(r["decision"] for r in res.values())
    matched = {i: r for i, r in res.items() if r["decision"] == "match"}
    adj = json.loads(Path("eval/match_adjudicated.json").read_text())
    def correct(i, r):
        verdict = adj.get(i, {}).get("truth")
        if r["pub_id"] == links[i]:
            return verdict != "matcher"
        return verdict in ("matcher", "either")
    right = sum(1 for i, r in matched.items() if correct(i, r))
    print(f"known links: {dict(c)}")
    print(f"(scored against the scans for {len(adj) - 1} adjudicated disagreements: eval/match_adjudicated.json)")
    print(f"accepted {len(matched)}: correct {right} -> precision {right / max(len(matched), 1):.2%}, "
          f"recall {right / len(links):.1%}")
    wrong = [(i, r) for i, r in matched.items() if not correct(i, r)]
    db = sqlite3.connect(ISFDB)
    t = lambda p: db.execute("SELECT pub_title FROM pubs WHERE pub_id = ?", (p,)).fetchone()[0]
    for i, r in wrong[:25]:
        print(f"  WRONG {i}: matched {t(r['pub_id'])} ({r['found']}/{r['testable']}, runner {r['runner_up']}); "
              f"ISFDB link says {t(links[i])}")
    Path("data/build/match_eval.json").write_text(json.dumps(res))


def fetch_ids():
    cands = json.loads(CANDIDATES.read_text())
    linked = {i for i, _ in known_links()}
    ids = sorted(i for i, c in cands.items() if c and i not in linked)
    Path("data/build/match_fetch_ids.txt").write_text("\n".join(ids) + "\n")
    print(f"{len(ids)} unlinked items with candidates")


def candidate_magazines(cands, ids):
    """item -> magazine names its candidate issues belong to (ISFDB series, else title minus date).
    Same naming as pipeline/export.py, so unindexed scans can be filed on the right magazine page."""
    from pipeline.export import clean, magazine_name
    issues = load_isfdb_issues(sqlite3.connect(ISFDB))
    out = {}
    for i in ids:
        out[i] = sorted({clean(magazine_name({"title": issues[p]["title"], "magazine": issues[p]["series"]}))
                         for p in cands.get(i, []) if p in issues})
    return out


def verify():
    cands = json.loads(CANDIDATES.read_text())
    linked = {i for i, _ in known_links()}
    ids = [i for i in Path("data/build/match_fetch_ids.txt").read_text().split() if i not in linked]
    res = verify_items(ids, cands)
    MATCHES.write_text(json.dumps({i: r for i, r in res.items() if r["decision"] == "match"}, indent=0))
    # Everything else, for the site's "also scanned, not indexed" lists.
    mags = candidate_magazines(cands, [i for i, r in res.items() if r["decision"] != "match"])
    unmatched = {i: {"decision": r["decision"], "magazines": mags[i]} for i, r in res.items() if r["decision"] != "match"}
    UNMATCHED.write_text(json.dumps(unmatched))
    c = Counter(r["decision"] for r in res.values())
    print(f"unlinked items: {dict(c)}; distinct issues matched: "
          f"{len({r['pub_id'] for r in res.values() if r['decision'] == 'match'})}")


if __name__ == "__main__":
    {"candidates": build_candidates, "eval": evaluate, "ids": fetch_ids, "verify": verify}[sys.argv[1]]()
