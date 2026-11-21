# z00l-File-Renamer Scrapers

This repository contains custom scrapers for **z00l-File-Renamer**. A scraper fetches metadata (title, date, performers, tags, cover, ...) from an external site, which the app then uses to rename your files.

Scrapers use the [Stash scraper format](https://docs.stashapp.cc/in-app-manual/scraping/scraper-development/) (`.yml`, XPath/JSON), the same one used by the Stash [CommunityScrapers](https://github.com/stashapp/CommunityScrapers), so they run unchanged in z00l-File-Renamer. This repository is also a *scraper source*: it is published as an `index.yml` plus one zip per scraper that the app's Scraper Manager can install from.

## Available scrapers

| Scraper | Site | Search by name | Scene URL |
|---|---|:---:|:---:|
| [ManyVids](scrapers/ManyVids) | manyvids.com | ✅ | ✅ |
| [ThatFetishGirl](scrapers/ThatFetishGirl) | thatfetishgirl.com | ✅ | ✅ |

## Installing scrapers via the Scraper Manager

1. In z00l-File-Renamer open **Metadata Settings** and go to the scraper installation tabs.
2. Add a new source with this URL:

   ```
   https://f0xlights.github.io/z00l-File-Renamer-Scrapers/index.yml
   ```
3. Install the scrapers you want. Tick *Fallback* on a scraper to also use it in the automatic text search.

Some scrapers may need extra setup (a proxy, an API key, a cookie). If so, the instructions are in comments at the top of the scraper's `.yml` file.

## Installing scrapers manually

Download the scraper's `.yml` from the [`scrapers`](scrapers) folder and install it like any other Stash scraper definition. Scrapers that depend on other files (for example Python scripts) need the whole folder.

## How to use scrapers

- **Search by name**: pick the scraper from *Scrape with...* (or enable it as a source) and let the app search with the file name or title.
- **Scene URL**: for scrapers that support it, paste the address of the scene page.

## Sites that block or geo-restrict access

Some sites show an age wall or block certain countries or user agents. If a scraper returns nothing or an error page, configure a proxy in the app (**Settings > Proxy Settings**, with *scrapers* enabled) and try again.

## Development

Requirements: Python 3.12+ and a copy of the z00l-File-Renamer program (the test runner reuses its engine, so it behaves exactly like the app, including its proxy settings).

```
scrapers/<Name>/<Name>.yml   # one folder per scraper (one package)
tools/build_index.py         # builds dist/index.yml and dist/<Name>.zip
tools/test_scraper.py        # runs a scraper through the app's own engine
```

Build the source locally:

```
python tools/build_index.py
```

Test a scraper with the Python of the z00l-File-Renamer installation (set `Z00L_APP` to its folder, or pass `--app`):

```
python tools/test_scraper.py ThatFetishGirl "Getting Acquainted" --app <path-to-z00l-File-Renamer>
python tools/test_scraper.py ThatFetishGirl --url "https://thatfetishgirl.com/access/scenes/Getting-Acquainted-With-Her-Neighbors-Asshole_vids.html" --app <path-to-z00l-File-Renamer>
```

Every push to `main` rebuilds and republishes the source on GitHub Pages (`.github/workflows/pages.yml`).

## Contributing a scraper

1. Create `scrapers/<Name>/<Name>.yml`. The capabilities the app uses are `sceneByName`, `sceneByURL` and `sceneByQueryFragment`.
2. Test it with `tools/test_scraper.py`: check search results, the scene page, and the date format.
3. Add a row to the table above and open a pull request.

Guidelines:

- No API keys or credentials in the repository. Document any required setup in a comment at the top of the scraper.
- One scraper per folder, self-contained.
- Put a short comment at the top of the `.yml` describing the site, the supported capabilities and any known limits.
