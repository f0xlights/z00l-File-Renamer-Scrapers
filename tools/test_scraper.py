#!/usr/bin/env python3
"""Runs a scraper from this repo through z00l-File-Renamer's own engine (same code, same proxy settings).

    <renamer venv python> tools/test_scraper.py ThatFetishGirl "Getting Acquainted" [--url URL]

Nothing is installed in the app: the .yml is loaded straight from scrapers/<name>/.
The app folder is read from --app or the Z00L_APP environment variable.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scraper", help="folder name in scrapers/")
    ap.add_argument("query", nargs="?", help="text to search")
    ap.add_argument("--url", help="scrape a scene page instead of searching")
    ap.add_argument("--app", default=os.environ.get("Z00L_APP"),
                    help="path of the z00l-File-Renamer folder (or set Z00L_APP)")
    ap.add_argument("--no-details", action="store_true", help="do not complete the first hit from its page")
    args = ap.parse_args()
    if not args.app:
        ap.error("the z00l-File-Renamer folder is required: use --app or set Z00L_APP")

    sys.path.insert(0, args.app)
    from sources import engine, manager     # noqa: E402  (the app's own modules)
    from core import network                # noqa: E402

    folder = ROOT / "scrapers" / args.scraper
    yml = next(iter(sorted(folder.glob("*.yml"))))
    doc = manager._yaml_load(yml.read_text(encoding="utf-8"))
    scraper = engine.CommunityScraper("custom", args.scraper, yml, doc)
    print(f"proxy active: {network.is_active()}")

    if args.url:
        scenes = [scraper.scrape_url(args.url)]
    else:
        scenes = scraper.search(args.query or "", loose=True)
        print(f"{len(scenes)} result(s)")
        if scenes and not args.no_details:
            scenes[0] = scraper.details(scenes[0])
    for i, s in enumerate(scenes[:8]):
        perf = ", ".join(p["performer"]["name"] for p in s.get("performers", []))
        print(f"[{i}] {s.get('date') or '----------'} | {s.get('title')} | {perf} | code={s.get('code')}")
        print(f"     {(s.get('urls') or [''])[0]}")
        if s.get("_complete") or args.url:
            print(f"     tags={[t['name'] for t in s.get('tags', [])][:6]}")
            print(f"     details={s.get('details', '')[:100]!r}")
            print(f"     image={(s.get('images') or [{}])[0].get('url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
