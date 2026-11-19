# F95 list page fixtures

Built from the live F95Zone markup checked on 2026-11-18 (see the
`import-f95-account-lists` design). Usernames, avatars and `_xfToken`
values are removed. Replace them with saved live pages when an account
with more than one page per list is available.

- `watched_page1.html`: Watched games, 1 row (with `.f95wg-ver`), plus a sidebar thread link.
- `bookmarks_page1.html`: Bookmarked games, 1 row (no `.f95wg-ver`).
- `past_end.html`: a page after the last one ("There are no threads to display.").
- `signed_out.html`: the login page (`data-logged-in="false"`).
- `challenge.html`: a Cloudflare challenge page.
