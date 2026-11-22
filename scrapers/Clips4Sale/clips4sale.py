#!/usr/bin/env python3
"""Clips4Sale scraper for z00l-File-Renamer (Stash "script" scraper protocol).

    clips4sale.py search   < {"name": "<text>"}                  -> [scene, ...]
    clips4sale.py url      < {"url": "<clip page url>"}          -> scene
    clips4sale.py details  < {"url"|"title"|"name": ...}         -> scene   (completes a search result)

The input is JSON on stdin, the output JSON on stdout, logs on stderr. The proxy of the program reaches this script
through the usual HTTP_PROXY / HTTPS_PROXY / ALL_PROXY variables. Nothing here talks to a Stash server.

Only the standard library is required. If `requests` is installed it is used instead (it also understands SOCKS proxies
when PySocks is installed).
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import requests
except Exception:                                   # standard library only
    requests = None

SITE = "https://www.clips4sale.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/143.0.0.0 Safari/537.36")
# the age/terms wall and language the site expects
COOKIES = "iAgreeWithUpdatedTerms=true; contentPreference=%5B0%5D; i18nextLng=en"
HEADERS = {"User-Agent": USER_AGENT, "Cookie": COOKIES}
MAX_RESULTS = 60


def log(message):
    print(f"[Clips4Sale] {message}", file=sys.stderr)


def load_banned_words():
    """Words that make Clips4Sale's search answer 404 (one per line in banned_words.txt)."""
    try:
        lines = (Path(__file__).parent / "banned_words.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {w.lower() for w in (l.strip() for l in lines) if w and not w.startswith("#")}


BANNED_WORDS = load_banned_words()


def clean_search_query(query):
    cleaned = re.sub(r"\w+", lambda m: "" if m.group(0).lower() in BANNED_WORDS else m.group(0), query)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# --------------------------------------------------------------------------- #
# Text helpers (no HTML library needed)
# --------------------------------------------------------------------------- #
def clean_text(text):
    """Descriptions come as HTML (sometimes escaped twice): plain text with paragraphs kept."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def parse_date(value):
    """Clips4Sale writes "01/02/06 3:04 PM": returns 2006-01-02, or ""."""
    try:
        return time.strftime("%Y-%m-%d", time.strptime(value or "", "%m/%d/%y %I:%M %p"))
    except ValueError:
        return ""


def absolute(url):
    return url if not url or url.startswith("http") else SITE + url


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


# --------------------------------------------------------------------------- #
# HTTP (requests when available, else urllib; both follow the proxy variables)
# --------------------------------------------------------------------------- #
def http_get(url, timeout=20, attempts=3):
    """(status, headers, text); retries rate limits and server errors."""
    status, headers, body = 0, {}, b""
    for attempt in range(1, attempts + 1):
        try:
            if requests is not None:
                r = requests.get(url, headers=HEADERS, timeout=timeout)
                status, headers, body = r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content
            else:
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
                        status, headers, body = r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
                except urllib.error.HTTPError as e:
                    status, headers, body = e.code, {k.lower(): v for k, v in e.headers.items()}, b""
        except Exception as e:
            log(f"request failed: {e}")
            status = 0
        if status in (0, 429, 500, 502, 503, 504) and attempt < attempts:
            time.sleep(attempt)
            continue
        break
    return status, headers, body.decode("utf-8", "replace")


def fetch(url):
    """Remix routes answer a redirect to the canonical address with an x-remix-redirect header and an empty 204 body
    instead of a 3xx: follow it once."""
    status, headers, text = http_get(url)
    redirect = headers.get("x-remix-redirect")
    if redirect:
        query = url.partition("?")[2]
        target = SITE + redirect
        status, headers, text = http_get(f"{target}?{query}" if query else target)
    return status, text


def fetch_json(url):
    """The first JSON document of a page (Remix may append more lines), or None."""
    status, text = fetch(url)
    if status != 200 or not text.strip():
        log(f"HTTP {status} for {url}")
        return None
    try:
        return json.loads(text.splitlines()[0])
    except ValueError:
        log(f"not JSON: {url}")
        return None


# --------------------------------------------------------------------------- #
# Clip -> scene
# --------------------------------------------------------------------------- #
def to_scene(clip):
    studio = {"name": dig(clip, "studio", "name")}
    performers = [{"name": name} for name in (dig(p, "stage_name") for p in clip.get("performers") or []) if name]

    names = [clip.get("category_name")]
    names += [r.get("category") for r in clip.get("related_category_links") or []]
    names += [k.get("keyword") for k in clip.get("keyword_links") or []]
    unique = {}
    for original in ((n or "").strip() for n in names):
        if not original:
            continue
        stored = unique.get(original.lower())
        if not stored or sum(c.isupper() for c in original) > sum(c.isupper() for c in stored):
            unique[original.lower()] = original

    scene = {"studio": studio}
    title = clean_text(clip.get("title"))
    if title:
        scene["title"] = title
    if clip.get("id"):
        scene["code"] = str(clip["id"])
    date = parse_date(clip.get("date_display"))
    if date:
        scene["date"] = date
    link = absolute(clip.get("link"))
    if link:
        scene["urls"] = [link]
    image = clip.get("cdn_previewlg_link") or clip.get("previewLink")
    if image:
        scene["image"] = image
    if performers:
        scene["performers"] = performers
    if unique:
        scene["tags"] = [{"name": t} for t in unique.values()]
    details = clean_text(clip.get("description"))
    if details:
        scene["details"] = details
    return scene


def dedupe_by_title(scenes):
    """The same clip is listed once per quality it was uploaded in: keep the first (best ranked) of each title."""
    seen, out = set(), []
    for scene in scenes:
        title = (scene.get("title") or "").strip().lower()
        if title and title in seen:
            continue
        seen.add(title)
        out.append(scene)
    return out


def search(query, limit=MAX_RESULTS):
    query = clean_search_query(query)
    if not query:
        return []
    url = (f"{SITE}/clips/search/{urllib.parse.quote(query)}/category/0/storesPage/1/clipsPage/1"
           "?_data=routes%2F%28%24lang%29.clips.search.%24")
    data = fetch_json(url)
    clips = [c for c in (dig(data, "clips") or []) if isinstance(c, dict)] if data else []
    return dedupe_by_title([to_scene(c) for c in clips])[:limit]


def scene_from_url(url):
    # https://www.clips4sale.com/studio/23235/22576135/some-clip-title-vid0541a
    m = re.search(r"/studio/(\d+)/(\d+)/([^/?#]+)", url or "")
    if not m:
        log(f"cannot read a clip from the address: {url}")
        return None
    studio_id, clip_id, slug = m.groups()
    data = fetch_json(f"{SITE}/studio/{studio_id}/{clip_id}/{slug}?_data=routes%2F%28%24lang%29.studio.%24id_.%24clipId.%24clipSlug")
    clip = dig(data, "clip")
    if isinstance(clip, dict):
        return to_scene(clip)
    # the page did not answer: look the clip up by its title words
    log("clip page unavailable, searching by its title")
    hits = search(slug.replace("-", " "), limit=20)
    for hit in hits:
        if hit.get("code") == clip_id:
            return hit
    return hits[0] if hits else None


def details(payload):
    candidates = [payload.get("url")] + list(payload.get("urls") or [])
    url = next((u for u in candidates if u and "clips4sale.com/studio/" in u), None)
    if url:
        return scene_from_url(url)
    title = str(payload.get("title") or payload.get("name") or "").strip()
    hits = search(title, limit=1) if title else []
    return hits[0] if hits else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    if mode == "search":
        out = search(str(payload.get("name") or payload.get("title") or "").strip())
    elif mode == "url":
        out = scene_from_url(str(payload.get("url") or ""))
    elif mode == "details":
        out = details(payload)
    else:
        log(f"unknown mode '{mode}' (use search, url or details)")
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
