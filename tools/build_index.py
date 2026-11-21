#!/usr/bin/env python3
"""Builds a Stash-style scraper source from scrapers/.

    python tools/build_index.py            # writes dist/index.yml and dist/<id>.zip

Every folder in scrapers/ is one package: all of its files go in the zip (the .yml files at the top level, which is
where z00l-File-Renamer looks for them). Point the app's Metadata Settings > Sources at dist/index.yml (a local path
or an https:// URL where dist/ is published).
"""
import hashlib
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scrapers"
DIST = ROOT / "dist"


def package_files(folder: Path):
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def build_zip(folder: Path, target: Path):
    """Deterministic zip (fixed timestamps, sorted names) so the checksum only changes when the content does."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in package_files(folder):
            info = zipfile.ZipInfo(f.relative_to(folder).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, f.read_bytes())


def scraper_name(folder: Path) -> str:
    for yml in sorted(folder.glob("*.yml")):
        m = re.search(r"^name:\s*(.+?)\s*$", yml.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip("\"'")
    return folder.name


def short_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=8", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out or "local"
    except Exception:
        return "local"


def main() -> int:
    folders = sorted(p for p in SRC.iterdir() if p.is_dir() and any(p.glob("*.yml")))
    if not folders:
        print("No scrapers found in scrapers/", file=sys.stderr)
        return 1
    DIST.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    version = f"{short_commit()}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    lines = []
    for folder in folders:
        zip_path = DIST / f"{folder.name}.zip"
        build_zip(folder, zip_path)
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        lines += [
            f"- id: {folder.name}",
            f"  name: {scraper_name(folder)}",
            "  metadata: {}",
            f"  version: {version}",
            f'  date: "{stamp}"',
            f"  path: {folder.name}.zip",
            f"  sha256: {sha}",
        ]
        print(f"built {zip_path.name} ({zip_path.stat().st_size} bytes)")
    (DIST / "index.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {DIST / 'index.yml'} ({len(folders)} package(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
