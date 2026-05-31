#!/usr/bin/env python3
"""scrape_nyt_spelling_bee_puzzles.py — pull every NYT Spelling Bee puzzle from sbsolver.com
into a CSV with the same columns as nyt_spelling_bee_puzzles.csv.

Usage
-----
    python scrape_nyt_spelling_bee_puzzles.py --out nyt_spelling_bee_puzzles.csv

What it does
------------
- Auto-detects the highest puzzle id (newest puzzle) from sbsolver's archive page.
- Walks back to puzzle id 1 (or `--start-id N`).
- Resumes from an existing CSV: any date already in the file is skipped.
- Computes the rank thresholds (0 / 2 / 5 / 8 / 15 / 25 / 40 / 50 / 70 / 100 % of
  Queen Bee, rounded half-up) — the formula was verified against six real NYT tables.
- Throttles requests (default 1.5 s pause), retries on transient errors, checkpoints
  to disk every 25 new puzzles, sorts the output by date.

CSV schema (matches nyt_spelling_bee_puzzles.csv)
-------------------------------------------------
    date, nyt_url_date, center, outer_letters, letters, words, pangrams, max_score,
    rank_beginner, rank_good_start, rank_moving_up, rank_good, rank_solid,
    rank_nice, rank_great, rank_amazing, rank_genius, rank_queen_bee

`date` is sbsolver's label; the NYT URL for the same puzzle is one day later
(`nyt_url_date`). `letters` is sorted uppercase; `outer_letters` is the six
non-centre letters, sorted.

Note on Cloudflare
------------------
sbsolver sits behind Cloudflare and will challenge fast/scripted access. The script
uses `cloudscraper` if installed, which clears the standard JS challenge for most
sessions; it falls back to plain `requests` (which will likely get blocked). If you
still get blocked, install `curl_cffi` and replace the fetcher, or run a headless
browser (Playwright). Keep the delay >= 1 s to stay friendly.
"""
import argparse
import csv
import datetime as dt
import re
import sys
import time
from pathlib import Path

# --- fetcher: prefer cloudscraper to clear Cloudflare; fall back to plain requests ---
try:
    import cloudscraper
    _SCRAPER = cloudscraper.create_scraper(
        browser={"browser": "firefox", "platform": "linux", "mobile": False}
    )
    _FETCHER = "cloudscraper"
except ImportError:
    import requests
    _SCRAPER = requests.Session()
    _FETCHER = "requests"

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("This script needs BeautifulSoup. Install with: pip install beautifulsoup4")

BASE = "https://www.sbsolver.com"
ARCHIVE_URL = BASE + "/archive"
PUZZLE_URL = BASE + "/s/{id}"

# NYT rank tiers (percentages verified against six real published tables).
LEVELS = [
    ("beginner", 0), ("good_start", 2), ("moving_up", 5), ("good", 8),
    ("solid", 15), ("nice", 25), ("great", 40), ("amazing", 50),
    ("genius", 70), ("queen_bee", 100),
]

CSV_COLS = (
    ["date", "nyt_url_date", "center", "outer_letters", "letters",
     "words", "pangrams", "max_score"]
    + ["rank_" + n for n, _ in LEVELS]
)


def half_up(pct, mx):
    """NYT's rounding: round(pct/100 * mx) with half-up rule. Queen Bee is the max itself."""
    return mx if pct == 100 else (pct * mx + 50) // 100


def fetch(url, retries=4, base_backoff=2.0):
    """GET with retries + exponential backoff. Returns text or None for 404."""
    last_err = None
    for attempt in range(retries):
        try:
            r = _SCRAPER.get(url, timeout=30, headers={
                "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Firefox/124.0 SpellingBeeArchive/1.0"),
            })
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(base_backoff * (2 ** attempt))
    raise RuntimeError(f"{url}: {last_err}")


def parse_puzzle(html, puzzle_id):
    """Return dict {id, date, center, letters, words, score, pangrams} or None."""
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.find("input", attrs={"name": "string"})
    if not inp or not inp.get("value"):
        return None
    raw = inp["value"]                         # e.g. "Taimnrv" — capital letter is the centre
    cap = re.search(r"[A-Z]", raw)
    if not cap:
        return None
    center = cap.group(0)
    letters = "".join(sorted(set(raw.upper())))
    if len(letters) != 7 or center not in letters:
        return None

    text = soup.get_text(" ", strip=True)
    date_m = re.search(r"(?:from|for) ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if not date_m:
        return None
    try:
        date = dt.datetime.strptime(date_m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None

    def grab(pat):
        m = re.search(pat, text)
        return int(m.group(1)) if m else None
    words = grab(r"words:\s*(\d+)")
    score = grab(r"score:\s*(\d+)")
    pangrams = grab(r"pangrams:\s*(\d+)")
    if None in (words, score, pangrams):
        return None
    return dict(id=puzzle_id, date=date, center=center, letters=letters,
                words=words, score=score, pangrams=pangrams)


def discover_max_id():
    """Return the largest puzzle id linked from the archive landing, or None."""
    html = fetch(ARCHIVE_URL)
    if not html:
        return None
    ids = [int(m.group(1)) for m in re.finditer(r"/s/(\d+)", html)]
    return max(ids) if ids else None


def to_row(p):
    """Build a CSV row matching nyt_spelling_bee_history.csv."""
    nyt = p["date"] + dt.timedelta(days=1)     # sbsolver labels puzzles one day before the NYT URL
    mx = int(p["score"])
    row = dict(
        date=p["date"].isoformat(),
        nyt_url_date=nyt.isoformat(),
        center=p["center"],
        outer_letters="".join(sorted(set(p["letters"]) - {p["center"]})),
        letters=p["letters"],
        words=int(p["words"]),
        pangrams=int(p["pangrams"]),
        max_score=mx,
    )
    for name, pct in LEVELS:
        row["rank_" + name] = half_up(pct, mx)
    return row


def load_existing(out_path):
    if not out_path.exists():
        return []
    with out_path.open() as f:
        return list(csv.DictReader(f))


def write_csv(rows, out_path):
    rows = sorted(rows, key=lambda r: r["date"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="nyt_spelling_bee_puzzles.csv", type=Path,
                    help="output CSV path (default: %(default)s)")
    ap.add_argument("--start-id", type=int, default=1,
                    help="oldest puzzle id to scrape (default: 1)")
    ap.add_argument("--end-id", type=int, default=None,
                    help="newest puzzle id (default: auto-detect from sbsolver archive)")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="seconds between requests (default: %(default)s)")
    ap.add_argument("--checkpoint-every", type=int, default=25,
                    help="flush CSV to disk every N new puzzles (default: %(default)s)")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore any existing CSV and start fresh")
    args = ap.parse_args()

    print(f"fetcher: {_FETCHER}", file=sys.stderr)
    end_id = args.end_id or discover_max_id()
    if not end_id:
        sys.exit("could not auto-detect the newest puzzle id; pass --end-id N")
    print(f"scraping s/{args.start_id} ... s/{end_id} -> {args.out}", file=sys.stderr)

    rows = [] if args.no_resume else load_existing(args.out)
    have_dates = {r["date"] for r in rows}
    new_count, failed = 0, []

    try:
        for pid in range(end_id, args.start_id - 1, -1):     # newest -> oldest
            try:
                html = fetch(PUZZLE_URL.format(id=pid))
                if html is None:
                    failed.append(pid); continue
                p = parse_puzzle(html, pid)
                if p is None:
                    failed.append(pid); continue
                if p["date"].isoformat() in have_dates:
                    print(f"  s/{pid}  {p['date']}  (already in CSV, skipped)", file=sys.stderr)
                    continue
                rows.append(to_row(p))
                have_dates.add(p["date"].isoformat())
                new_count += 1
                print(f"  s/{pid}  {p['date']}  {p['letters']}/{p['center']}  "
                      f"{p['words']:>3}w  {p['score']:>4}pts  {p['pangrams']}p",
                      file=sys.stderr)
                if new_count % args.checkpoint_every == 0:
                    write_csv(rows, args.out)
                    print(f"  -- checkpoint: {len(rows)} rows --", file=sys.stderr)
            except Exception as e:
                print(f"  s/{pid}: {e}", file=sys.stderr)
                failed.append(pid)
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\ninterrupted — saving what we have", file=sys.stderr)

    write_csv(rows, args.out)
    print(f"\nwrote {len(rows)} rows to {args.out}", file=sys.stderr)
    if failed:
        head = failed[:30]
        more = "" if len(failed) <= 30 else f" ... (+{len(failed) - 30} more)"
        print(f"failed/empty ids ({len(failed)}): {head}{more}", file=sys.stderr)


if __name__ == "__main__":
    main()
