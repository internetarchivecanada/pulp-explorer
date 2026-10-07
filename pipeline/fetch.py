"""Fetch the per-item files needed to turn printed pages into BookReader pages.

    python -m pipeline.fetch                 # every linked item in data/build/pulp.sqlite
    python -m pipeline.fetch --limit 10      # a quick trial
    python -m pipeline.fetch --ids FILE      # other items, e.g. matcher candidates

Per item, into data/raw/ia/items/<id>/:
  files.json          the item's file list (one metadata API request)
  scandata.xml        leaf order and which leaves BookReader shows
  page_numbers.json   archive.org's printed-page detection, when it exists
  pagetext.json.gz    page-level OCR text, from hOCR (2 small files, kept too) or,
                      failing that, from _djvu.xml (about 4 MB, deleted after conversion)

Politeness (see CLAUDE.md): at most 4 requests in flight, starting at 2 and adding one
after 50 straight successes. 429/502/503/504 and connection errors halve the limit and
pause every worker (Retry-After honoured). A plain 500 is retried for that file alone,
then logged to data/raw/ia/fetch_failures.log. A 404 leaves a .missing marker. Writes
are atomic, and anything already on disk is never requested again.
"""
import argparse
import gzip
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

ROOT = Path("data/raw/ia/items")
FAILURES = Path("data/raw/ia/fetch_failures.log")
UA = "pulp-explorer/0.1 (Wayback Machine Labs; brian.eggert@archive.org)"
MAX_INFLIGHT, START_INFLIGHT, GROW_AFTER = 4, 2, 50
LOAD_SIGNALS = {429, 502, 503, 504}


class Missing(Exception):
    pass


class Throttle:
    def __init__(self):
        self.cond = threading.Condition()
        self.limit, self.inflight, self.streak, self.paused_until = START_INFLIGHT, 0, 0, 0.0

    def acquire(self):
        with self.cond:
            while True:
                wait = self.paused_until - time.time()
                if wait > 0:
                    self.cond.wait(wait)
                elif self.inflight >= self.limit:
                    self.cond.wait(1)
                else:
                    self.inflight += 1
                    return

    def release(self, ok, pause=0.0):
        with self.cond:
            self.inflight -= 1
            if ok:
                self.streak += 1
                if self.streak >= GROW_AFTER and self.limit < MAX_INFLIGHT:
                    self.limit, self.streak = self.limit + 1, 0
            elif pause:
                self.limit, self.streak = max(1, self.limit // 2), 0
                self.paused_until = max(self.paused_until, time.time() + pause)
                print(f"  load signal: limit {self.limit}, pausing {pause:.0f}s", flush=True)
            self.cond.notify_all()


throttle = Throttle()
log_lock = threading.Lock()


def get(url):
    """GET with the shared throttle. Returns bytes; raises Missing on 404."""
    backoff, server_errors = 10.0, 0
    while True:
        throttle.acquire()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            throttle.release(True)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                throttle.release(True)
                raise Missing(url)
            if e.code in LOAD_SIGNALS:
                retry_after = e.headers.get("Retry-After", "")
                pause = float(retry_after) if retry_after.isdigit() else backoff
                throttle.release(False, pause)
                backoff = min(backoff * 2, 600)
                continue
            throttle.release(False)
            server_errors += 1
            if server_errors >= 3:
                raise
            time.sleep(5 * server_errors)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            throttle.release(False, backoff)
            backoff = min(backoff * 2, 600)


def save(path, data):
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_file(ia_id, remote, local):
    """Download one file unless it (or its .missing marker) is already on disk."""
    marker = local.with_name(local.name + ".missing")
    if local.exists():
        return True
    if marker.exists():
        return False
    url = f"https://archive.org/download/{ia_id}/{urllib.parse.quote(remote)}"
    try:
        save(local, get(url))
        return True
    except Missing:
        marker.touch()
        return False


def djvu_pages(xml_bytes):
    x = xml_bytes.decode("utf-8", "replace")
    pages = []
    for obj in x.split("<OBJECT ")[1:]:
        words = re.findall(r"<WORD[^>]*>([^<]*)</WORD>", obj)
        pages.append(unescape(" ".join(words)))
    return pages


def hocr_pages(d):
    text = gzip.decompress((d / "hocr_searchtext.txt.gz").read_bytes()).decode("utf-8", "replace")
    index = json.loads(gzip.decompress((d / "hocr_pageindex.json.gz").read_bytes()))
    return [text[a:b] for a, b, *_ in index]


def fetch_item(ia_id):
    d = ROOT / ia_id
    d.mkdir(parents=True, exist_ok=True)
    try:
        if not (d / "files.json").exists():
            meta = json.loads(get(f"https://archive.org/metadata/{ia_id}/files"))
            if "result" not in meta:
                (d / "files.json.missing").touch()
                return ia_id, "no-metadata"
            save(d / "files.json", json.dumps(meta["result"]).encode())
        names = {f["name"] for f in json.loads((d / "files.json").read_text())}

        def have(suffix):
            """Derived files are named after the uploaded file, which isn't always the identifier.
            Use the single match, or the one named after the identifier; several others is ambiguous."""
            matches = [n for n in names if n.endswith(suffix) and "/" not in n]
            if f"{ia_id}{suffix}" in matches:
                return f"{ia_id}{suffix}"
            return matches[0] if len(matches) == 1 else None

        for suffix, local in (("_scandata.xml", "scandata.xml"), ("_page_numbers.json", "page_numbers.json")):
            if have(suffix):
                fetch_file(ia_id, have(suffix), d / local)

        out = d / "pagetext.json.gz"
        if out.exists():
            return ia_id, "cached"
        if have("_hocr_searchtext.txt.gz") and have("_hocr_pageindex.json.gz"):
            ok = fetch_file(ia_id, have("_hocr_searchtext.txt.gz"), d / "hocr_searchtext.txt.gz") and \
                fetch_file(ia_id, have("_hocr_pageindex.json.gz"), d / "hocr_pageindex.json.gz")
            if ok:
                save(out, gzip.compress(json.dumps({"source": "hocr", "pages": hocr_pages(d)}).encode()))
                return ia_id, "hocr"
        if have("_djvu.xml"):
            url = f"https://archive.org/download/{ia_id}/{urllib.parse.quote(have('_djvu.xml'))}"
            pages = djvu_pages(get(url))
            save(out, gzip.compress(json.dumps({"source": "djvu", "pages": pages}).encode()))
            return ia_id, "djvu"
        (d / "pagetext.json.gz.missing").touch()
        return ia_id, "no-text"
    except Exception as e:
        with log_lock, FAILURES.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{ia_id}\t{e!r}\n")
        return ia_id, "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="file of identifiers, one per line (default: ISFDB-linked items)")
    args = ap.parse_args()
    if args.ids:
        ids = Path(args.ids).read_text().split()
    else:
        ids = [r[0] for r in sqlite3.connect("data/build/pulp.sqlite").execute(
            "SELECT DISTINCT ia_id FROM items ORDER BY ia_id")]
    if args.limit:
        ids = ids[:args.limit]
    ROOT.mkdir(parents=True, exist_ok=True)
    counts, start = {}, time.time()
    with ThreadPoolExecutor(MAX_INFLIGHT) as pool:
        for n, (ia_id, status) in enumerate(pool.map(fetch_item, ids), 1):
            counts[status] = counts.get(status, 0) + 1
            if n % 25 == 0 or n == len(ids):
                rate = n / (time.time() - start)
                print(f"{n}/{len(ids)} items  {rate * 3600:.0f}/h  limit {throttle.limit}  {counts}", flush=True)
    print("done", counts)


if __name__ == "__main__":
    sys.exit(main())
