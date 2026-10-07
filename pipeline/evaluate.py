"""Hand-check sample for page links, and the precision report.

    python -m pipeline.evaluate sample [N]     -> eval/sample.csv (stratified by method, seeded)
    python -m pipeline.evaluate report eval/sample.csv

Verdict column (fill in by opening `link` or looking at `image`):
  Y   the story/feature starts on this page (a splash illustration or the title page counts)
  A   adjacent: the start is one page before or after
  N   wrong page
  ?   can't tell (missing or unreadable scan)
Precision = Y / (Y + A + N); "within one page" = (Y + A) / (Y + A + N).
"""
import csv
import random
import sqlite3
import sys
from pathlib import Path

DB = Path("data/build/pulp.sqlite")
SEED = 20261005
QUOTAS = {"label": 0.5, "folio": 0.5}


def sample(n=100):
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT l.ia_id, l.n, l.page, l.method, l.checked, c.ttype, c.title, i.title,
               (SELECT GROUP_CONCAT(a.name, ' & ') FROM credits cr JOIN authors a USING(author_id)
                WHERE cr.title_id = c.title_id)
        FROM links l JOIN contents c USING(pub_id, title_id) JOIN issues i USING(pub_id)
        WHERE l.method IN ('label', 'folio') AND c.ttype IN ('SHORTFICTION', 'SERIAL', 'NOVEL', 'ESSAY')
        ORDER BY l.ia_id, l.title_id""").fetchall()
    rng = random.Random(SEED)
    picked = []
    for method, share in QUOTAS.items():
        pool = [r for r in rows if r[3] == method]
        picked += rng.sample(pool, min(len(pool), round(n * share)))
    Path("eval").mkdir(exist_ok=True)
    out = Path("eval/sample.csv")
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ia_id", "n", "printed_page", "method", "title_check", "type", "title", "authors",
                    "issue", "link", "image", "verdict", "notes"])
        for ia_id, nn, page, method, checked, ttype, title, issue, authors in picked:
            w.writerow([ia_id, nn, page, method, checked, ttype, title, authors, issue,
                        f"https://archive.org/details/{ia_id}/page/n{nn}",
                        f"https://archive.org/download/{ia_id}/page/n{nn}_w500.jpg", "", ""])
    print(f"wrote {len(picked)} rows to {out}")


def report(path):
    rows = list(csv.DictReader(open(path)))
    groups = {}
    for r in rows:
        v = r["verdict"].strip().upper()
        if v in ("Y", "A", "N"):
            for key in ("all", r["method"]):
                groups.setdefault(key, []).append(v)
    for key, vs in groups.items():
        t = len(vs)
        print(f"{key:6} n={t:3}  exact {vs.count('Y') / t:.0%}  within one page {(t - vs.count('N')) / t:.0%}")
    print(f"unlabelled or '?': {sum(1 for r in rows if r['verdict'].strip().upper() not in ('Y', 'A', 'N'))}")


if __name__ == "__main__":
    if sys.argv[1] == "sample":
        sample(int(sys.argv[2]) if len(sys.argv) > 2 else 100)
    else:
        report(sys.argv[2])
