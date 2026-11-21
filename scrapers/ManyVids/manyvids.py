#!/usr/bin/env python3
"""ManyVids scraper for z00l-File-Renamer (Stash "script" scraper protocol).

    manyvids.py search   < {"name": "<text>"}            -> [scene, ...]
    manyvids.py url      < {"url": "<video page url>"}   -> scene
    manyvids.py details  < {"code"|"id"|"url": ...}      -> scene   (completes a search result: tags, date, ...)

The input is JSON on stdin, the output JSON on stdout, logs on stderr. The proxy of the program reaches this script
through the usual HTTP_PROXY / HTTPS_PROXY / ALL_PROXY variables.

Only the standard library is required. If `requests` is installed it is used instead (it also understands SOCKS proxies
when PySocks is installed).
"""
import html
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import requests
except Exception:                                   # standard library only
    requests = None

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/129.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://www.manyvids.com/", "Origin": "https://www.manyvids.com"}
VIDEO_API = "https://api.manyvids.com/store/video/{}"
SEARCH_API = "https://www.manyvids.com/bff/search/results"
AUTOCOMPLETE_API = "https://www.manyvids.com/bff/search/autocomplete"
CATALOG_API = "https://api.manyvids.com/store/videos/{}"
VIDEO_PAGE = "https://www.manyvids.com/Video/{}/"

MAX_RESULTS = 60              # the program shows at most this many results of a search: send the best ones
CATALOG_MAX_PAGES = 100
CATALOG_TTL = 15 * 60         # a downloaded catalog is reused by the next searches of a batch for this long
CACHE_DIR = os.path.join(tempfile.gettempdir(), "z00l-manyvids-cache")

_ID_RE = re.compile(r"(?<!\d)(\d{6,8})(?!\d)")
_PROFILE_ID = re.compile(r"/Profile/(\d+)/")
_VIDEO_URL_ID = re.compile(r"manyvids\.com/video/(\d+)", re.I)


def log(message):
    print(f"[ManyVids] {message}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# HTTP (requests when available, else urllib; both follow the proxy variables)
# --------------------------------------------------------------------------- #
def get_json(url, params=None, timeout=15, attempts=3):
    """JSON of a GET, or None (unknown address, error). Retries rate limits and server errors."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(1, attempts + 1):
        try:
            if requests is not None:
                r = requests.get(url, headers=HEADERS, timeout=timeout)
                status, body = r.status_code, r.content
            else:
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
                        status, body = r.status, r.read()
                except urllib.error.HTTPError as e:
                    status, body = e.code, b""
            if status == 200:
                return json.loads(body.decode("utf-8", "replace"))
            if status in (429, 500, 502, 503, 504) and attempt < attempts:
                time.sleep(attempt)
                continue
            log(f"HTTP {status} for {url}")
            return None
        except Exception as e:
            if attempt >= attempts:
                log(f"request failed for {url}: {e}")
                return None
            time.sleep(attempt)
    return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def words(s):
    return set(re.findall(r"[^\W_]+", (s or "").lower()))


def unescape(value):
    return html.unescape(value) if isinstance(value, str) else value


def looks_like_date(digits):
    """8 digits that read as YYYYMMDD are far more likely a date in the filename than a video id."""
    if len(digits) != 8:
        return False
    try:
        datetime.strptime(digits, "%Y%m%d")
        return 1990 <= int(digits[:4]) <= 2100
    except ValueError:
        return False


def find_video_ids(text, limit=2):
    """Candidate video ids (6-8 digits, e.g. 3424528) written in a filename or search text."""
    ids = []
    for m in _ID_RE.finditer(text or ""):
        if not looks_like_date(m.group(1)) and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids[:limit]


def iso_date(raw):
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return str(raw)[:10]


def to_scene(vid, fallback_artist=""):
    """A ManyVids video object -> the scene dict the program reads."""
    vid_id = str(vid.get("id"))
    creator = vid.get("creator") or vid.get("model") or {}
    if not isinstance(creator, dict):
        creator = {}
    name = creator.get("stageName") or creator.get("displayName") or creator.get("username") or fallback_artist or "Unknown"
    name = unescape(name)

    desc = vid.get("description") or vid.get("title") or ""
    img = vid.get("thumbnail")
    if isinstance(img, dict):
        img = img.get("url")
    img = img or vid.get("screenshot")
    if isinstance(img, str) and img.startswith("http:"):
        img = "https:" + img[5:]

    tags = [{"name": unescape(t.get("label"))} for t in (vid.get("tagList") or [])
            if isinstance(t, dict) and t.get("label")]
    scene = {
        "title": unescape(vid.get("title")),
        "code": vid_id,
        "url": VIDEO_PAGE.format(vid_id),
        "details": unescape(desc),
        "date": iso_date(vid.get("launchDate")),
        "studio": {"name": name},
        "performers": [{"name": name, "gender": "FEMALE"}],
        "images": [img] if img else [],
    }
    if tags:
        scene["tags"] = tags
    return scene


def fetch_video(video_id):
    """Full video data from the store API, or None."""
    d = (get_json(VIDEO_API.format(video_id), timeout=10) or {}).get("data")
    return d if isinstance(d, dict) and str(d.get("id")) == str(video_id) else None


# --------------------------------------------------------------------------- #
# Creator lookup
# --------------------------------------------------------------------------- #
def find_creator_autocomplete(artist):
    """Creator whose name equals `artist`, from ManyVids' autocomplete (reliable for names the fuzzy search buries)."""
    target = norm(artist)
    d = get_json(AUTOCOMPLETE_API, {"keywords": artist, "contentPref": 0}, timeout=10)
    if not isinstance(d, dict):
        return None
    for entry in (d.get("all") or []) + (d.get("models") or []):
        if not isinstance(entry, dict):
            continue
        m = _PROFILE_ID.search(entry.get("url") or "")
        names = [n for n in (entry.get("username"), entry.get("stage_name"), entry.get("stageName"),
                             entry.get("displayName")) if isinstance(n, str)]
        if m and any(norm(n) == target for n in names):
            return {"id": m.group(1), "displayName": names[0]}
    return None


def find_creator(artist):
    """The creator whose name equals `artist` (case/space/punctuation-insensitive), or None. Never "the first result":
    ManyVids' search is fuzzy and the first hit is often a different creator."""
    target = norm(artist)
    if len(target) < 3:
        return None
    found = find_creator_autocomplete(artist)
    if found:
        return found
    d = get_json(SEARCH_API, {"keywords": artist, "contentPref": 0, "limit": 30, "offset": 0}, timeout=10)
    if not isinstance(d, dict):
        return None
    for vid in (d.get("vids") or []) + (d.get("storeItems") or []):
        c = vid.get("creator") or vid.get("model")
        if isinstance(c, dict) and any(isinstance(n, str) and norm(n) == target
                                       for n in (c.get("stageName"), c.get("displayName"), c.get("username"))):
            return c
    return None


# --------------------------------------------------------------------------- #
# Creator catalog (cached on disk: every search is a separate process)
# --------------------------------------------------------------------------- #
_lock = threading.Lock()


def _cache_path(creator_id):
    return os.path.join(CACHE_DIR, f"catalog_{creator_id}.json")


def load_cached_catalog(creator_id):
    try:
        path = _cache_path(creator_id)
        if time.time() - os.path.getmtime(path) < CATALOG_TTL:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def save_cached_catalog(creator_id, items):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(creator_id) + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp, _cache_path(creator_id))
    except Exception as e:
        log(f"could not cache the catalog: {e}")


def download_catalog(creator_id):
    cached = load_cached_catalog(creator_id)
    if cached is not None:
        log(f"catalog of creator {creator_id}: {len(cached)} videos (cached)")
        return cached

    def fetch(page):
        return (get_json(CATALOG_API.format(creator_id), {"sort": "newest", "page": page}, timeout=10) or {}).get("data") or []

    items, page = [], 1
    with ThreadPoolExecutor(max_workers=5) as pool:
        while page <= CATALOG_MAX_PAGES:
            batch = list(pool.map(fetch, range(page, min(page + 5, CATALOG_MAX_PAGES + 1))))
            done = False
            for chunk in batch:
                if not chunk:
                    done = True
                    break
                items.extend(chunk)
            if done:
                break
            page += 5
    if items:
        save_cached_catalog(creator_id, items)
    log(f"catalog of creator {creator_id}: {len(items)} videos")
    return items


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def search(query):
    log(f"searching for: {query}")
    artist_name, title_part, creator_id = "", "", None
    if " - " in query:
        parts = query.split(" - ")
        artist_name = parts[0].strip()
        title_part = parts[1].strip() if len(parts) > 1 else ""
    else:
        # No "Artist - Title" separator ("EveYourApple step Mommys Sweaty Pussy Ass"): find where the creator name
        # ends by testing the first 1..4 words as a creator name.
        ws = query.split()
        for k in range(1, min(4, len(ws)) + 1):
            cand = " ".join(ws[:k])
            if find_video_ids(cand):
                continue                       # a video id is not part of a creator name
            c = find_creator(cand)
            if c:
                artist_name, title_part, creator_id = cand, " ".join(ws[k:]), c.get("id")
                log(f"resolved creator '{cand}' (id {creator_id}), title '{title_part}'")
                break
        else:
            artist_name = query

    results, seen = [], set()

    def add(items, label):
        for vid in items:
            try:
                vid_id = str(vid.get("id"))
                if vid_id in seen:
                    continue
                seen.add(vid_id)
                scene = to_scene(vid, artist_name)
                scene["_label"] = label
                results.append(scene)
            except Exception as e:
                log(f"skipped a malformed item: {e}")

    # Strategy 0: a video id in the name is an exact match: look it up directly.
    for vid_id in find_video_ids(query):
        data = fetch_video(vid_id)
        if data:
            log(f"video id {vid_id}: {data.get('title')}")
            add([data], "ID")
            if not re.search(r"[A-Za-z]{2,}", _ID_RE.sub(" ", query)):
                return finish(results, query, artist_name, creator_id)        # the query is only the id

    # Strategy A: keyword search for the artist (4 pages in parallel)
    if artist_name:
        def fetch_a(offset):
            d = get_json(SEARCH_API, {"keywords": artist_name, "contentPref": 0, "limit": 30, "offset": offset}, timeout=10)
            return ((d or {}).get("vids") or []) + ((d or {}).get("storeItems") or [])
        with ThreadPoolExecutor(max_workers=4) as pool:
            for items in pool.map(fetch_a, (0, 30, 60, 90)):
                add(items, "Search")

    # Strategy B: the title on its own
    if title_part:
        d = get_json(SEARCH_API, {"keywords": title_part, "contentPref": 0, "limit": 30, "offset": 0}, timeout=10)
        add(((d or {}).get("vids") or []) + ((d or {}).get("storeItems") or []), "Title")

    # Strategy C: the creator's whole catalog. Only a creator whose name equals the artist is trusted.
    if not creator_id and artist_name:
        c = find_creator(artist_name)
        if c:
            creator_id = c.get("id")
    if creator_id:
        add(download_catalog(creator_id), "Store-API")

    return finish(results, query, artist_name, creator_id)


def finish(results, query, artist_name, creator_id):
    # The fuzzy search also returns other creators when the keyword is only in a title: keep the artist's own videos.
    artist_resolved = bool(creator_id) or " - " in query
    if artist_name and results and artist_resolved:
        target = artist_name.lower().replace(" ", "")
        kept = []
        for sc in results:
            if sc["_label"] in ("Store-API", "ID"):
                kept.append(sc)                                    # the store API / an id lookup are always right
                continue
            studio = sc["studio"]["name"].lower().replace(" ", "")
            if studio and (target in studio or studio in target):
                kept.append(sc)
        if len(kept) != len(results):
            log(f"removed {len(results) - len(kept)} results from other creators")
        results = kept

    # The program keeps the first MAX_RESULTS: a whole catalog is long, so put the likeliest scenes first.
    wanted = words(re.sub(r"\s-\s", " ", query)) - words(artist_name if creator_id else "")
    ids = set(find_video_ids(query))

    def score(sc):
        if sc["code"] in ids:
            return 2.0
        title = words(sc["title"])
        return len(wanted & title) / (len(wanted) or 1) + (0.5 if wanted and title and title <= wanted else 0)

    results.sort(key=score, reverse=True)
    out = results[:MAX_RESULTS]
    for sc in out:
        sc.pop("_label", None)
    log(f"{len(out)} of {len(results)} results")
    return out


# --------------------------------------------------------------------------- #
# Details / URL
# --------------------------------------------------------------------------- #
def details(payload):
    vid_id = str(payload.get("code") or payload.get("id") or "")
    if not vid_id.isdigit():
        m = _VIDEO_URL_ID.search(str(payload.get("url") or ""))
        vid_id = m.group(1) if m else ""
    if not vid_id:
        return None
    data = fetch_video(vid_id)
    return to_scene(data) if data else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    if mode == "search":
        out = search(str(payload.get("name") or payload.get("title") or "").strip())
    elif mode in ("url", "details"):
        out = details(payload)
    else:
        log(f"unknown mode '{mode}' (use search, url or details)")
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
