# Game Catalog

The catalog is a shared SQLite database of adult games, built on the project
VPS by `catalog_server/` and published to a Cloudflare R2 bucket served at
`https://wlib-db.thekirin.net`. wLib downloads it from there; clients never
contact the VPS. The builder is not part of the app and is never bundled into
PyInstaller, the AppImage or other packages.

## Data sources

| Source | What is collected | How |
|---|---|---|
| F95Zone | Games in the public `latest_data` feed | 90 per page, at most 90 requests per rolling hour, 3 s apart |
| DLsite | Adult (`age_category == 3`) games from `/maniax/` (doujin) and `/pro/` (commercial PC) | Listing pages for IDs, circles and genres, then `product/info/ajax` for 100 IDs per request, 3 s apart |
| Ryuugames | Posts from the WordPress REST API | 100 per page, 3 s apart; title, developer, version, categories and store IDs are kept from the post body; download links are never stored |
| VNDB | Visual novels with an 18+ release, and adult releases that link to DLsite or Steam | Official kana API, at most 150 requests per 5 minutes (waited out within a run), 1 s apart; refreshed weekly |

Every request carries the user agent `wLib-catalog-builder/1
(+https://github.com/kirin-3/wLib)`. The builder never logs in to any site and
stores no download links or game files. Timeouts and 5xx answers get up to three
attempts with a 30-second backoff. A 429 or a rate-limit message pauses that
source for the rest of the run; the other sources continue.

Links between sites come only from what a source states: a DLsite RJ/VJ code,
Steam app ID or VNDB ID in a Ryuugames post, and DLsite or Steam store links on
a VNDB release. Targets that are catalog sources must exist in the catalog.
There is no title matching; the `origin` column leaves room for it later.

### VNDB attribution

VNDB data comes from [The Visual Novel Database](https://vndb.org/) and is
available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The VNDB-derived tables in the snapshot (`vndb_vns`, `vndb_releases` and links
whose `origin` is `vndb`) are distributed under the same license. wLib's
Settings → Game catalog block names VNDB and the license.

### F95 tag and prefix names

The feed gives numeric tag and prefix IDs. `catalog_server/data/f95_tags.json`
maps them to names and is loaded into `f95_tag_names` and `f95_prefix_names`
on every run. IDs missing from the map are still stored and simply have no
name. To refresh the map, save the logged-in `https://f95zone.to/sam/latest_alpha/`
page from your own browser and run:

```bash
python -m catalog_server.tools.import_f95_tags saved-page.html
```

## Snapshot and manifest

Bucket layout:

- `v1/manifest.json`, `Cache-Control: public, max-age=300`;
- `v1/snapshots/catalog-<UTC timestamp>.sqlite.xz`, `Cache-Control: public, max-age=31536000, immutable`.

A snapshot is a `VACUUM INTO` copy of `catalog.sqlite`, compressed with xz
preset 6. It is uploaded first; the manifest is replaced only after that upload
succeeds. Retention then keeps the four newest snapshots, so the manifest's
snapshot and the three before it stay available.

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-27T05:12:09Z",
  "snapshot": {
    "path": "v1/snapshots/catalog-20261027T051209Z.sqlite.xz",
    "size": 4123456,
    "sha256": "…",
    "uncompressed_size": 31000000
  },
  "sources": {
    "f95": {"count": 27990, "updated_at": "…"},
    "dlsite": {"count": 69191, "updated_at": "…"},
    "ryuugames": {"count": 18240, "updated_at": "…"},
    "vndb": {"count": 31902, "updated_at": "…"}
  }
}
```

`snapshot.path` is relative to the manifest's base URL. `count` is the number of
entries not marked removed; `updated_at` is the source's last successful run.

### Snapshot tables

| Table | Key | Notes |
|---|---|---|
| `f95_threads` | `thread_id` | title, creator, version, `updated_at`/`ts`, views, likes, rating, `tags` and `prefixes` (JSON ID lists), cover and thread URL |
| `dlsite_works` | `product_id` | site, title, circle and circle ID, work type, genres (JSON), release date, cover and work URL |
| `ryuu_posts` | `post_id` | cleaned and raw title, developer, version, categories, `rj_codes`, `steam_ids`, `vndb_ids` (JSON), dates, cover and post URL |
| `vndb_vns` | `vn_id` | title, original title, developers, release date, original language, non-spoiler tags rated 1.0 or higher, cover |
| `vndb_releases` | `release_id` | VN IDs, DLsite and Steam IDs from the store links |
| `links` | all columns | `src_a, id_a, src_b, id_b, origin` |
| `f95_tag_names`, `f95_prefix_names` | `id` | names from `f95_tags.json` |
| `meta` | `key` | `schema_version`, `built_at` |

Every source row has `first_seen`, `last_seen` and `removed_at`. When a full
sweep of a source completes, rows missing from it and from the sweep before it
get `removed_at`. One missed sweep is not enough, because a deletion on an
earlier page of a live feed can shift a row past the sweep. A row that appears
again has `removed_at` cleared. Rows are never deleted.

`origin` is NULL for rows a source listed itself and set on rows imported
from elsewhere. Sweeps never mark imported rows removed; once the
source lists one, its `origin` is cleared and it is treated like any other row.

### Schema versions

- `core/catalog_format.py` holds what the builder and the app share: the
  schema version, `SUPPORTED_SCHEMA_VERSIONS`, the manifest field names and the
  `meta` keys. It imports nothing else from the app.
- Adding a table or column keeps `schema_version`; clients ignore what they
  don't read.
- A breaking change raises the version and publishes under a new prefix
  (`v2/`). Keep publishing `v1/` until old clients have had time to update.
- The app refuses snapshots whose version it does not list.

## Running the builder

```bash
python -m catalog_server run                      # collect from all sources, then publish
python -m catalog_server run --no-publish         # collect only
python -m catalog_server run --sources ryuugames,vndb --no-publish
python -m catalog_server run --sources none       # publish the current catalog only
python -m catalog_server run --until-swept all --no-publish   # first full load
python -m catalog_server status                   # recent runs, cursors, counts
```

`--data-dir` (before the command) or `WLIB_CATALOG_DATA_DIR` picks the data
folder; the default is `/var/lib/wlib-catalog`. It holds `catalog.sqlite` (what
gets published) and `state.sqlite` (cursors, high-water marks, the request log
and run history; never published). A `run.lock` in that folder prevents
overlapping runs: a second invocation logs that another run is in progress and
exits 0.

Each run does, for every selected source, its incremental fetch and then
advances the full-listing sweep. F95 sweeps with whatever is left of its
hourly budget, so it re-crawls continuously. DLsite and Ryuugames have no
budget; VNDB waits out its 150-request, five-minute budget within a run.
DLsite, Ryuugames and VNDB start a new full sweep only once a week after the
last one completed. Interrupted sweeps resume from their cursor. `--until-swept` waits out request budgets
instead of stopping, so the first F95 load (about 311 pages) takes about 3.5 hours.
The exit code is 1 when a source failed, 2 when publishing was requested
without R2 credentials.

Publishing needs `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
and optionally `R2_BUCKET` (default `wlib-db`). The token must only be
able to edit objects in that bucket. Logs name object keys and sizes, never
credentials. The builder's only extra dependency is `boto3`, in
`catalog_server/requirements.txt`.

## VPS operation

Setup (as root, from a checkout):

```bash
bash catalog_server/deploy/setup.sh
```

It installs git, xz and Python 3; the selected interpreter must be 3.12 or newer (`WLIB_CATALOG_PYTHON` overrides the default `python3`). It creates the `wlibcat` system user and `/var/lib/wlib-catalog`, clones the repository into `/opt/wlib-catalog`, creates the venv, installs `wlib-catalog.service` and `wlib-catalog.timer`, and leaves the timer off. Put the token in `/etc/wlib-catalog/r2.env` (root-owned, mode 0600):

```
R2_ACCOUNT_ID=…
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
R2_BUCKET=wlib-db
```

Manual runs as the service user:

```bash
sudo -u wlibcat env WLIB_CATALOG_DATA_DIR=/var/lib/wlib-catalog \
  /opt/wlib-catalog/.venv/bin/python -m catalog_server status
```

Run from `/opt/wlib-catalog` so `catalog_server` is importable.

- Schedule: `systemctl enable --now wlib-catalog.timer` (05:00 and 17:00 UTC,
  up to 15 minutes late). `systemctl list-timers wlib-catalog.timer` shows the
  next run.
- Logs: `journalctl -u wlib-catalog.service`; per-source results are also in
  `status`.
- Update: rerun `setup.sh`; it resets the checkout to the branch and reinstalls
  the units.

Rollback:

- Stop publishing: `systemctl disable --now wlib-catalog.timer`. The last
  snapshot stays available.
- Withdraw a bad snapshot: upload the previous `manifest.json` again. Its
  snapshot is still in the bucket because retention keeps four.
- In the app, turning the catalog off deletes the local copy.

## Client page lookup

`core/game_lookup.resolve_page(url, ids)` resolves a page's exact IDs, catalog
metadata, and matching local game IDs. The catalog connection uses `mode=ro`
and closes after each call; a missing, disabled, or unreadable catalog falls
back to the page's own IDs. No request is made to the sites during lookup.

The resolver reads `dlsite_works.product_id`, `title`, `circle`, `genres`,
`cover_url`; `vndb_vns.vn_id`, `title`, `developers`, `tags`, `cover_url`; and
`ryuu_posts.post_id`, `url`, `title`, `developer`, `version`, `cover_url`,
`rj_codes`, `steam_ids`, `vndb_ids`. JSON lists are decoded into metadata or
identities. Source rows with `removed_at` set are ignored. WordPress permalinks
can resolve to a numeric post ID through `ryuu_posts.url`; without a catalog,
the extension supplies the ID from the live page.

`links(src_a, id_a, src_b, id_b, origin)` is searched in both directions for
one hop from the page's own identity. Newly found IDs are never expanded
again. F95 thread identity wins when present; otherwise exact matchable store
IDs are used, without title matching. Multiple matching game IDs are returned
without merging or changing any game.
