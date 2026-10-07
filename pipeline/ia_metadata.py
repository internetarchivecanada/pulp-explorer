"""Bulk-fetch item metadata for an archive.org collection via the scrape API.

    python -m pipeline.ia_metadata pulpmagazinearchive data/raw/ia/pulpmagazinearchive.jsonl

A handful of requests (10k items per page), not one per item. Resumable: pages
already fetched are skipped via the cursor log kept next to the output.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

FIELDS = ["identifier", "title", "date", "year", "volume", "issue", "subject", "creator",
          "publisher", "language", "imagecount", "collection", "description", "series"]
UA = "pulp-explorer/0.1 (Wayback Machine Labs; brian.eggert@archive.org)"


def fetch(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            wait = 2 ** (i + 2)
            print(f"  {e}; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit("giving up")


def main(collection, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cursors = out.with_suffix(".cursors")
    done = cursors.read_text().split() if cursors.exists() else []
    if done and done[-1] == "END":
        print("already complete")
        return
    cursor = done[-1] if done else None
    while True:
        q = {"q": f"collection:{collection}", "fields": ",".join(FIELDS), "count": 10000}
        if cursor:
            q["cursor"] = cursor
        page = fetch("https://archive.org/services/search/v1/scrape?" + urllib.parse.urlencode(q))
        with out.open("a") as f:
            for item in page.get("items", []):
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        cursor = page.get("cursor")
        with cursors.open("a") as f:
            f.write((cursor or "END") + "\n")
        print(f"{len(page.get('items', []))} items (total {page.get('total')})")
        if not cursor:
            break
        time.sleep(1)


if __name__ == "__main__":
    main(*sys.argv[1:3])
