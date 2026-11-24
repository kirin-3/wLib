// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import {
  DISCOVER_VIEW_STORAGE_KEY,
  activeDiscoverFilterCount,
  clearDiscoverFilters,
  cycleSite,
  cycleTag,
  defaultDiscoverView,
  discoverCountLabel,
  discoverCoverUrl,
  discoverQuery,
  discoverQueryKey,
  normalizeDiscoverView,
  readDiscoverView,
  siteState,
  tagState,
  toggleEngine,
  toggleStatus,
  writeDiscoverView,
} from "./discoverView.ts";

const memoryStorage = (): Storage => {
  const items = new Map<string, string>();
  return {
    get length() { return items.size; },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
};

test("the default view sorts by last update with no filters", () => {
  const state = defaultDiscoverView();
  assert.equal(state.sort, "updated");
  assert.equal(activeDiscoverFilterCount(state), 0);
  assert.deepEqual(discoverQuery(state, "  echo  "), {
    search: "echo",
    include_sites: [],
    exclude_sites: [],
    include_tags: [],
    exclude_tags: [],
    statuses: [],
    engines: [],
    min_rating: null,
    hide_library: false,
    sort: "updated",
    offset: 0,
    limit: 120,
  });
});

test("site pills cycle include, exclude and neutral", () => {
  let state = cycleSite(defaultDiscoverView(), "f95");
  assert.equal(siteState(state, "f95"), "include");
  state = cycleSite(state, "f95");
  assert.equal(siteState(state, "f95"), "exclude");
  assert.deepEqual(discoverQuery(state, "").exclude_sites, ["f95"]);
  state = cycleSite(state, "f95");
  assert.equal(siteState(state, "f95"), "neutral");
  assert.equal(activeDiscoverFilterCount(state), 0);
});

test("tag pills compare without case and punctuation", () => {
  let state = cycleTag(defaultDiscoverView(), "big tits");
  assert.equal(tagState(state, "Big-Tits"), "include");
  state = cycleTag(state, "BIG TITS");
  assert.deepEqual(state.includeTags, []);
  assert.deepEqual(state.excludeTags, ["BIG TITS"]);
});

test("status and engine toggles, rating and filter count", () => {
  let state = toggleStatus(defaultDiscoverView(), "completed");
  state = toggleEngine(state, "Ren'Py");
  state = { ...state, minRating: "4.0", hideLibrary: true };
  assert.equal(activeDiscoverFilterCount(state), 4);
  const query = discoverQuery(state, "", 240);
  assert.deepEqual(query.statuses, ["completed"]);
  assert.deepEqual(query.engines, ["Ren'Py"]);
  assert.equal(query.min_rating, 4);
  assert.equal(query.offset, 240);
  assert.equal(toggleStatus(state, "completed").statuses.length, 0);
});

test("clearing filters keeps the sort and the pane", () => {
  const state = { ...cycleSite(defaultDiscoverView(), "lc"), sort: "title" as const, filtersOpen: false, hideLibrary: true };
  const cleared = clearDiscoverFilters(state);
  assert.equal(activeDiscoverFilterCount(cleared), 0);
  assert.equal(cleared.sort, "title");
  assert.equal(cleared.filtersOpen, false);
});

test("view choices round-trip through storage", () => {
  const storage = memoryStorage();
  const state = { ...toggleStatus(cycleTag(cycleSite(defaultDiscoverView(), "dlsite"), "sandbox"), "onhold"), sort: "rating" as const, hideLibrary: true };
  writeDiscoverView(state, storage);
  assert.deepEqual(readDiscoverView(storage), state);
  assert.match(storage.getItem(DISCOVER_VIEW_STORAGE_KEY) ?? "", /"version":1/);
});

test("stored values that are unknown or broken fall back to defaults", () => {
  const storage = memoryStorage();
  storage.setItem(DISCOVER_VIEW_STORAGE_KEY, "{not json");
  assert.deepEqual(readDiscoverView(storage), defaultDiscoverView());
  const state = normalizeDiscoverView({
    version: 1,
    includeSites: ["f95", "nope", 3, "f95"],
    excludeSites: ["f95", "vndb"],
    statuses: ["completed", "done"],
    minRating: "1.0",
    sort: "random",
    hideLibrary: "yes",
  });
  assert.deepEqual(state.includeSites, ["f95"]);
  assert.deepEqual(state.excludeSites, ["vndb"]);
  assert.deepEqual(state.statuses, ["completed"]);
  assert.equal(state.minRating, "");
  assert.equal(state.sort, "updated");
  assert.equal(state.hideLibrary, false);
  assert.deepEqual(normalizeDiscoverView({ version: 99, sort: "title" }), defaultDiscoverView());
});

test("the query key ignores the page", () => {
  const state = defaultDiscoverView();
  assert.equal(discoverQueryKey(discoverQuery(state, "a", 0)), discoverQueryKey(discoverQuery(state, "a", 360)));
  assert.notEqual(discoverQueryKey(discoverQuery(state, "a")), discoverQueryKey(discoverQuery(state, "b")));
});

test("cover URLs use the F95 preview host and never LewdCorner", () => {
  assert.equal(discoverCoverUrl("https://attachments.f95zone.to/2023/01/a.jpg"), "https://preview.f95zone.to/2023/01/a.jpg");
  assert.equal(discoverCoverUrl("https://lewdcorner.com/attachments/a.1/"), "");
  assert.equal(discoverCoverUrl("https://cdn.lewdcorner.com/a.jpg"), "");
  assert.equal(discoverCoverUrl("javascript:alert(1)"), "");
  assert.equal(discoverCoverUrl("https://t.vndb.org/cv/1.jpg"), "https://t.vndb.org/cv/1.jpg");
});

test("count labels", () => {
  assert.equal(discoverCountLabel(1), "1 game");
  assert.equal(discoverCountLabel(12345), "12,345 games");
});
