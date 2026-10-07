"""Write the static JSON the site reads.

    python -m pipeline.export            -> site/data/

  search.json             [[kind, id, label, detail, weight], ...] for autocomplete:
                          kind 'a' author (pen names point at the real author),
                          'w' work (id = author page + work anchor), 'm' magazine
  authors/<id>.json       an author's scanned appearances, grouped by work
  issues/<pub_id>.json    an issue's contents with page links
  magazines.json          magazine -> issues by date, plus "other": scans filed under the
                          magazine but not matched to an issue (uploader's title, year, reason)
  stats.json              counts for the home page

Page links: methods label, folio, folio-ext link to the start page; 'issue' links to
the scan's first page and is marked approximate (`exact: false`). Only text contents
(fiction, essays, poems, reviews, editorials, interviews) are exported.
"""
import html
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path("data/build/pulp.sqlite")
UNMATCHED = Path("data/build/unmatched.json")
IA_META = Path("data/raw/ia/pulpmagazinearchive.jsonl")
OUT = Path("site/data")
PAGE_METHODS = {"label", "folio", "folio-ext"}
PLACEHOLDER_AUTHORS = {"uncredited", "unknown", "The Editor", "Anonymous"}  # no author page
FICTION = {"SHORTFICTION", "SERIAL", "NOVEL", "POEM"}
TYPE_LABEL = {"SHORTFICTION": "story", "SERIAL": "serial", "NOVEL": "novel", "POEM": "poem",
              "ESSAY": "essay", "EDITOR": "editor", "REVIEW": "review", "INTERVIEW": "interview"}
DATE_TAIL = re.compile(r",\s*[^,]*\b(1[89]|20)\d\d\b.*$")


def magazine_name(issue):
    """ISFDB's magazine series when known, else the issue title minus its date."""
    if issue.get("magazine"):
        return issue["magazine"]
    issue_title = issue["title"]
    name = DATE_TAIL.sub("", issue_title).strip()
    name = re.sub(r"\s*[,#]?\s*(v\d+|vol\.?\s*\d+|#\d+|no\.?\s*\d+).*$", "", name, flags=re.I).strip(" ,")
    return name or issue_title


DISAMBIG = re.compile(r"\s*\([^()]*\b(?:18|19|20)\d\d\)$")


def clean(s):
    """ISFDB stores non-Latin-1 text as HTML entities; decode them."""
    return html.unescape(s) if s and "&" in s else s


def display_title(t):
    """Drop ISFDB's disambiguation suffix: 'Letter (Weird Tales, December 1932)' -> 'Letter'."""
    return DISAMBIG.sub("", clean(t)) or clean(t)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if OUT.exists():
        shutil.rmtree(OUT)

    authors = {}
    for r in db.execute("SELECT * FROM authors"):
        a = dict(r)
        a["name"], a["legal_name"] = clean(a["name"]), clean(a["legal_name"])
        authors[a["author_id"]] = a
    real_of = defaultdict(set)    # alias -> real people
    aliases_of = defaultdict(set)  # real person -> aliases
    for r in db.execute("SELECT real_id, alias_id FROM pseudonyms"):
        real_of[r["alias_id"]].add(r["real_id"])
        aliases_of[r["real_id"]].add(r["alias_id"])
    credits = defaultdict(list)
    for r in db.execute("SELECT title_id, author_id FROM credits ORDER BY author_id"):
        credits[r["title_id"]].append(r["author_id"])
    issues = {r["pub_id"]: dict(r) for r in db.execute("SELECT * FROM issues")}
    for iss in issues.values():
        iss["title"], iss["magazine"] = clean(iss["title"]), clean(iss["magazine"])
    scans = defaultdict(list)
    auto = {}  # ia_id -> evidence, for scans the matcher linked
    for r in db.execute("SELECT ia_id, pub_id, link_source, evidence FROM items ORDER BY link_source = 'auto', ia_id"):
        scans[r["pub_id"]].append(r["ia_id"])
        if r["link_source"] == "auto":
            auto[r["ia_id"]] = r["evidence"]
    work_title = {r["title_id"]: display_title(r["title"]) for r in db.execute(
        "SELECT title_id, title FROM contents WHERE title_id = work_id")}

    links = defaultdict(dict)  # (pub_id, title_id) -> ia_id -> (n, method)
    for r in db.execute("SELECT ia_id, pub_id, title_id, n, method FROM links"):
        links[(r["pub_id"], r["title_id"])][r["ia_id"]] = (r["n"], r["method"])

    def scan_links(pub_id, title_id):
        out = []
        for ia_id in scans[pub_id]:
            n, method = links[(pub_id, title_id)].get(ia_id, (0, "issue"))
            exact = method in PAGE_METHODS
            link = {"ia": ia_id, "n": n if exact else None, "exact": exact}
            if ia_id in auto:
                link["auto"] = auto[ia_id]
            out.append(link)
        return out

    types = ",".join(f"'{t}'" for t in TYPE_LABEL)
    contents = [dict(r) for r in db.execute(f"""
        SELECT pub_id, title_id, work_id, page, ttype, title FROM contents
        WHERE ttype IN ({types}) ORDER BY pub_id, CAST(page AS INTEGER), title""")]
    for c in contents:
        c["title"] = display_title(c["title"])
    editors = defaultdict(list)  # EDITOR records name the issue's editor; they aren't contents
    for c in contents:
        if c["ttype"] == "EDITOR":
            editors[c["pub_id"]] += [[a, authors[a]["name"]] for a in credits[c["title_id"]] if a in authors]
    contents = [c for c in contents if c["ttype"] != "EDITOR"]
    bylines = {a for c in contents for a in credits[c["title_id"]]}

    # Issues
    by_issue = defaultdict(list)
    for c in contents:
        by_issue[c["pub_id"]].append(c)
    magazines = defaultdict(list)
    for pub_id, iss in issues.items():
        items = []
        for c in by_issue[pub_id]:
            items.append({
                "t": c["title"], "type": TYPE_LABEL[c["ttype"]], "page": c["page"].split("|")[0] if c["page"] else "",
                "by": [[a, authors[a]["name"]] for a in credits[c["title_id"]] if a in authors],
                "work": c["work_id"], "scans": scan_links(pub_id, c["title_id"]),
            })
        dump(OUT / "issues" / f"{pub_id}.json", {
            "id": pub_id, "title": iss["title"], "date": iss["date"], "year": iss["year"],
            "magazine": magazine_name(iss), "scans": scans[pub_id], "contents": items,
            "auto": {ia: auto[ia] for ia in scans[pub_id] if ia in auto},
            "editors": editors[pub_id],
        })
        magazines[magazine_name(iss)].append([pub_id, iss["title"], iss["date"] or ""])

    # Authors: an appearance is listed under the work's canonical author(s); the printed
    # byline (which may be a pen name) is kept per appearance.
    by_author = defaultdict(lambda: defaultdict(list))
    for c in contents:
        canon = credits.get(c["work_id"]) or credits.get(c["title_id"]) or []
        iss = issues[c["pub_id"]]
        appearance = {
            "issue": c["pub_id"], "issue_title": iss["title"], "date": iss["date"], "year": iss["year"],
            "printed_title": c["title"], "type": TYPE_LABEL[c["ttype"]],
            "page": c["page"].split("|")[0] if c["page"] else "",
            "as": [authors[a]["name"] for a in credits[c["title_id"]] if a in authors and a not in canon],
            "scans": scan_links(c["pub_id"], c["title_id"]),
        }
        for a in canon:
            if a in authors and authors[a]["name"] not in PLACEHOLDER_AUTHORS:
                by_author[a][c["work_id"]].append(appearance)

    search, weight = [], {}
    for a, works in by_author.items():
        info = authors[a]
        wl = []
        for w, apps in works.items():
            apps.sort(key=lambda x: (x["date"] or "9999"))
            title = work_title.get(w) or apps[0]["printed_title"]
            wl.append({"id": w, "title": title, "type": apps[0]["type"], "apps": apps,
                       "fiction": apps[0]["type"] in {TYPE_LABEL[t] for t in FICTION}})
            if wl[-1]["fiction"]:
                search.append(["w", f"{a}/w{w}", title, info["name"], len(apps)])
        wl.sort(key=lambda x: (x["apps"][0]["date"] or "9999", x["title"]))
        dump(OUT / "authors" / f"{a}.json", {
            "id": a, "name": info["name"], "legal_name": info["legal_name"],
            "birth": (info["birth"] or "")[:4].strip("0") and info["birth"][:4],
            "death": (info["death"] or "")[:4].strip("0") and info["death"][:4],
            "pen_names": sorted({authors[x]["name"] for x in aliases_of[a] if x in authors and x in bylines}),
            "pen_name_of": sorted([[r, authors[r]["name"]] for r in real_of[a] if r in authors and r in by_author]),
            "works": wl,
        })
        n_apps = sum(len(w["apps"]) for w in wl)
        search.append(["a", str(a), info["name"], f"{n_apps} appearance{'s' * (n_apps != 1)}", n_apps])
        weight[a] = n_apps

    # Pen names printed on bylines in these scans, with no works credited to them directly,
    # point at the real author. (ISFDB also lists spelling variants; unused ones are skipped.)
    for alias, reals in real_of.items():
        if alias in authors and alias not in by_author and alias in bylines:
            for r in reals:
                if r in by_author:
                    search.append(["a", str(r), authors[alias]["name"], f"pen name of {authors[r]['name']}", weight[r] * 0.9])

    # Scans the matcher considered but couldn't confirm: listed on their magazine's page under
    # the uploader's own title, only when every candidate issue belongs to that one magazine.
    other = defaultdict(list)
    n_other = 0
    if UNMATCHED.exists():
        from pipeline.match import parse_date
        meta = {d["identifier"]: d for d in map(json.loads, IA_META.open())}
        for ia_id, u in json.loads(UNMATCHED.read_text()).items():
            if len(u["magazines"]) != 1 or ia_id not in meta:
                continue
            d = meta[ia_id]
            year = parse_date(f"{ia_id} {d.get('title', '')}", d.get("date"))[0]
            reason = "no readable text" if u["decision"] == "no-text" else "issue not confirmed"
            other[u["magazines"][0]].append([ia_id, d.get("title") or ia_id, year, reason])
            n_other += 1
    mags = []
    for name in set(magazines) | set(other):
        iss = sorted(magazines.get(name, []), key=lambda x: x[2])
        oth = sorted(other.get(name, []), key=lambda x: (x[2] or 9999, x[1]))
        mags.append({"name": name, "issues": iss, "other": oth})
        detail = f"{len(iss)} issue{'s' * (len(iss) != 1)}" + (f", {len(oth)} more scanned" if oth else "")
        search.append(["m", name, name, detail, len(iss) + len(oth) * .5])
    mags.sort(key=lambda m: (-len(m["issues"]), -len(m["other"]), m["name"]))
    dump(OUT / "magazines.json", mags)
    dump(OUT / "search.json", search)

    n_links = sum(1 for c in contents for s in scan_links(c["pub_id"], c["title_id"]) if s["exact"])
    years = [i["year"] for i in issues.values() if i["year"]]
    stats = {"other_scans": n_other, "issues": len(issues), "scans": sum(len(v) for v in scans.values()), "auto_scans": len(auto),
             "auto_issues": sum(1 for p, v in scans.items() if v and all(i in auto for i in v)),
             "authors": len(by_author),
             "works": len({c["work_id"] for c in contents}), "contents": len(contents), "exact_links": n_links,
             "magazines": len(mags), "first_year": min(years), "last_year": max(years),
             "isfdb_backup": "2025-02-01"}
    dump(OUT / "stats.json", stats)
    print(json.dumps(stats))
    print(f"search entries: {len(search)}")


if __name__ == "__main__":
    main()
