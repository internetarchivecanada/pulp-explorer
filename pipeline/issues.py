"""Join archive.org pulp items to ISFDB issues and their contents.

    python -m pipeline.issues

Inputs:  data/build/isfdb.sqlite, data/raw/ia/pulpmagazinearchive.jsonl
Output:  data/build/pulp.sqlite with tables
  items(ia_id, pub_id, link_source, n_pubs, evidence)
                                                one row per linked archive.org item; link_source is
                                                'webpage' or 'note' (ISFDB editors) or 'auto'
                                                (pipeline/match.py, evidence = stories found)
  issues(pub_id, title, date, year, magazine)   the linked ISFDB magazine issues; magazine is
                                                the ISFDB series of the issue's EDITOR record
  contents(pub_id, title_id, work_id, page, ttype, title)
  credits(title_id, author_id)                  author credits (ca_status 1) as printed
  authors(author_id, name, legal_name, birth, death)
  pseudonyms(real_id, alias_id)

`work_id` is the canonical title (ISFDB title_parent, else the title itself), so
retitlings and pen-name credits of the same story group together. `link_source`
records where the link came from ('webpage' or 'note'). Links are kept exactly as
ISFDB editors made them; nothing here is guessed.
"""
import json
import re
import sqlite3
from pathlib import Path

BUILD = Path("data/build")
ISFDB = BUILD / "isfdb.sqlite"
IA_META = Path("data/raw/ia/pulpmagazinearchive.jsonl")
OUT = BUILD / "pulp.sqlite"
MATCHES = BUILD / "matches.json"

DETAILS = re.compile(r"archive\.org/details/([A-Za-z0-9._-]+)")
# Content types worth showing; cover art, interior art etc. stay in the DB but are flagged.
TEXT_TYPES = ("SHORTFICTION", "SERIAL", "NOVEL", "ESSAY", "POEM", "EDITOR", "REVIEW", "INTERVIEW")


def find_links(src, collection):
    links = {}  # (ia_id, pub_id) -> source
    q_web = """SELECT w.pub_id, w.url FROM webpages w JOIN pubs p USING(pub_id)
               WHERE p.pub_ctype = 'MAGAZINE' AND w.url LIKE '%archive.org/details/%'"""
    q_note = """SELECT p.pub_id, n.note_note FROM pubs p JOIN notes n ON n.note_id = p.note_id
                WHERE p.pub_ctype = 'MAGAZINE' AND n.note_note LIKE '%archive.org/details/%'"""
    for source, query in (("webpage", q_web), ("note", q_note)):
        for pub_id, text in src.execute(query):
            for ia_id in DETAILS.findall(text):
                if ia_id in collection:
                    links.setdefault((ia_id, pub_id), source)
    return links


def main():
    collection = {json.loads(line)["identifier"] for line in IA_META.open()}
    src = sqlite3.connect(ISFDB)
    links = find_links(src, collection)
    evidence = {}
    if MATCHES.exists():  # automatic matches, only for items ISFDB hasn't linked
        linked_items = {i for i, _ in links}
        for ia_id, m in json.loads(MATCHES.read_text()).items():
            if ia_id not in linked_items and ia_id in collection:
                links[(ia_id, m["pub_id"])] = "auto"
                evidence[ia_id] = f"{m['found']} of {m['testable']} stories found in the scan"
    pubs_per_item = {}
    for ia_id, pub_id in links:
        pubs_per_item[ia_id] = pubs_per_item.get(ia_id, 0) + 1

    tmp = OUT.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE items(ia_id, pub_id, link_source, n_pubs, evidence, PRIMARY KEY(ia_id, pub_id));
        CREATE TABLE issues(pub_id PRIMARY KEY, title, date, year, magazine);
        CREATE TABLE contents(pub_id, title_id, work_id, page, ttype, title);
        CREATE TABLE credits(title_id, author_id, PRIMARY KEY(title_id, author_id));
        CREATE TABLE authors(author_id PRIMARY KEY, name, legal_name, birth, death);
        CREATE TABLE pseudonyms(real_id, alias_id, PRIMARY KEY(real_id, alias_id));
    """)
    db.executemany("INSERT INTO items VALUES (?,?,?,?,?)",
                   [(i, p, s, pubs_per_item[i], evidence.get(i)) for (i, p), s in sorted(links.items())])

    pub_ids = sorted({p for _, p in links})
    src.execute("CREATE TEMP TABLE want(pub_id INTEGER PRIMARY KEY)")
    src.executemany("INSERT INTO want VALUES (?)", [(p,) for p in pub_ids])

    magazine = dict(src.execute("""
        SELECT pc.pub_id, MIN(s.series_title) FROM pub_content pc JOIN want USING(pub_id)
        JOIN titles t ON t.title_id = pc.title_id JOIN series s ON s.series_id = t.series_id
        WHERE t.title_ttype = 'EDITOR' GROUP BY pc.pub_id"""))
    for pub_id, title, date in src.execute(
            "SELECT pub_id, pub_title, pub_year FROM pubs JOIN want USING(pub_id)"):
        year = int(date[:4]) if date and date[:4].isdigit() and date[:4] != "0000" else None
        db.execute("INSERT INTO issues VALUES (?,?,?,?,?)", (pub_id, title, date, year, magazine.get(pub_id)))

    rows = src.execute("""
        SELECT pc.pub_id, t.title_id, CASE WHEN t.title_parent > 0 THEN t.title_parent ELSE t.title_id END,
               pc.pubc_page, t.title_ttype, t.title_title
        FROM pub_content pc JOIN want USING(pub_id) JOIN titles t ON t.title_id = pc.title_id""").fetchall()
    db.executemany("INSERT INTO contents VALUES (?,?,?,?,?,?)", rows)

    title_ids = {r[1] for r in rows} | {r[2] for r in rows}
    src.execute("CREATE TEMP TABLE want_t(title_id INTEGER PRIMARY KEY)")
    src.executemany("INSERT INTO want_t VALUES (?)", [(t,) for t in title_ids])
    credits = src.execute("""SELECT DISTINCT ca.title_id, ca.author_id FROM canonical_author ca
                             JOIN want_t USING(title_id) WHERE ca.ca_status = 1""").fetchall()
    db.executemany("INSERT INTO credits VALUES (?,?)", credits)

    # Authors credited, plus the real people behind any pen names (and their other pen names).
    author_ids = {a for _, a in credits}
    pseud = src.execute("SELECT author_id, pseudonym FROM pseudonyms").fetchall()
    real_ids = {r for r, a in pseud if a in author_ids}
    pseud = [(r, a) for r, a in pseud if r in real_ids or a in author_ids]
    db.executemany("INSERT INTO pseudonyms VALUES (?,?)", pseud)
    author_ids |= {r for r, _ in pseud} | {a for _, a in pseud}
    src.execute("CREATE TEMP TABLE want_a(author_id INTEGER PRIMARY KEY)")
    src.executemany("INSERT INTO want_a VALUES (?)", [(a,) for a in author_ids])
    db.executemany("INSERT INTO authors VALUES (?,?,?,?,?)", src.execute("""
        SELECT author_id, author_canonical, author_legalname, author_birthdate, author_deathdate
        FROM authors JOIN want_a USING(author_id)"""))

    db.executescript("""
        CREATE INDEX contents_pub ON contents(pub_id);
        CREATE INDEX contents_work ON contents(work_id);
        CREATE INDEX credits_author ON credits(author_id);
    """)
    db.commit()
    db.close()
    tmp.replace(OUT)

    out = sqlite3.connect(OUT)
    q = lambda s: out.execute(s).fetchone()[0]
    auto_sql = "SELECT COUNT(*) FROM items WHERE link_source = 'auto'"
    print(f"auto-matched items: {q(auto_sql)}")
    print(f"items: {q('SELECT COUNT(DISTINCT ia_id) FROM items')} "
          f"(linked to >1 issue: {q('SELECT COUNT(DISTINCT ia_id) FROM items WHERE n_pubs > 1')})")
    print(f"issues: {q('SELECT COUNT(*) FROM issues')}")
    types = ",".join(f"'{t}'" for t in TEXT_TYPES)
    print(f"text contents: {q(f'SELECT COUNT(*) FROM contents WHERE ttype IN ({types})')}, "
          f"with page: {q(f'''SELECT COUNT(*) FROM contents WHERE ttype IN ({types}) AND page GLOB '[0-9]*' ''')}")
    print(f"distinct works: {q(f'SELECT COUNT(DISTINCT work_id) FROM contents WHERE ttype IN ({types})')}")
    print(f"authors: {q('SELECT COUNT(*) FROM authors')}, pen-name links: {q('SELECT COUNT(*) FROM pseudonyms')}")


if __name__ == "__main__":
    main()
