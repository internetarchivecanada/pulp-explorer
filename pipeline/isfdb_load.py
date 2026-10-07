"""Load the tables we need from an ISFDB MySQL dump into SQLite.

    python -m pipeline.isfdb_load data/raw/isfdb/isfdb-2025-02-01.sql data/build/isfdb.sqlite

No MySQL server needed: CREATE TABLE column lists and INSERT rows are parsed
straight from the dump. Rows are inserted as-is (strings unescaped).
"""
import re
import sqlite3
import sys
from pathlib import Path

TABLES = {
    "pubs", "pub_content", "titles", "authors", "canonical_author", "pub_authors",
    "pseudonyms", "webpages", "identifiers", "identifier_types", "notes", "series",
    "pub_series", "publishers", "title_relationships",
}

TOKEN = re.compile(r"""'((?:[^'\\]|\\.)*)'|(NULL)|(-?[0-9][0-9.eE+-]*)|([(),;])""", re.S)
ESC = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "b": "\b"}
UNESC = re.compile(r"\\(.)", re.S)


def unescape(s):
    return UNESC.sub(lambda m: ESC.get(m.group(1), m.group(1)), s) if "\\" in s else s


def parse_rows(values_sql):
    row, depth = None, 0
    for m in TOKEN.finditer(values_sql):
        s, null, num, punct = m.groups()
        if punct == "(":
            row, depth = [], 1
        elif punct == ")":
            yield row
            row, depth = None, 0
        elif punct in (",", ";") or punct is None and row is None:
            continue
        elif s is not None:
            row.append(unescape(s))
        elif null:
            row.append(None)
        else:
            row.append(int(num) if num.lstrip("-").isdigit() else float(num))


def main(dump, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    cols, current, counts = {}, None, {}
    with open(dump, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("CREATE TABLE"):
                current = line.split("`")[1]
                cols[current] = []
            elif current and line.startswith("  `"):
                cols[current].append(line.split("`")[1])
            elif line.startswith(") ENGINE"):
                t = current
                current = None
                if t in TABLES:
                    db.execute(f"DROP TABLE IF EXISTS {t}")
                    db.execute(f"CREATE TABLE {t} ({', '.join(cols[t])})")
            elif line.startswith("INSERT INTO `"):
                t = line.split("`")[1]
                if t not in TABLES:
                    continue
                n = len(cols[t])
                rows = [r for r in parse_rows(line[line.index("VALUES") + 6:]) if len(r) == n]
                db.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * n)})", rows)
                counts[t] = counts.get(t, 0) + len(rows)
    for stmt in [
        "CREATE INDEX pc_pub ON pub_content(pub_id)", "CREATE INDEX pc_title ON pub_content(title_id)",
        "CREATE INDEX ca_title ON canonical_author(title_id)", "CREATE INDEX pa_pub ON pub_authors(pub_id)",
        "CREATE INDEX wp_pub ON webpages(pub_id)", "CREATE INDEX id_pub ON identifiers(pub_id)",
        "CREATE INDEX pubs_ctype ON pubs(pub_ctype)", "CREATE UNIQUE INDEX notes_id ON notes(note_id)",
        "CREATE INDEX ca_author ON canonical_author(author_id)",
        "CREATE UNIQUE INDEX pubs_id ON pubs(pub_id)", "CREATE UNIQUE INDEX titles_id ON titles(title_id)",
        "CREATE UNIQUE INDEX authors_id ON authors(author_id)",
    ]:
        db.execute(stmt)
    db.commit()
    db.close()
    tmp.replace(out)
    for t in sorted(counts):
        print(f"{t:22} {counts[t]:>10,}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
