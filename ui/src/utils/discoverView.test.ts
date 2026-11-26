// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import {
  DISCOVER_VIEW_STORAGE_KEY,
  activeDiscoverFilterCount,
  clearDiscoverFilters,
  defaultDiscoverView,
  describeDiscoverNoResults,
  discoverCountLabel,
  discoverCoverUrl,
  discoverFilterChips,
  discoverQuery,
  discoverQueryKey,
  discoverResultsView,
  discoverSectionCounts,
  discoverSiteNames,
  discoverSourceSummary,
  discoverStatusLabel,
  discoverTagList,
  excludeSite,
  excludeTag,
  includeSite,
  includeTag,
  normalizeDiscoverView,
  readDiscoverView,
  removeDiscoverFilter,
  siteState,
  tagState,
  toggleDiscoverSection,
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

test("a site pill click toggles include; exclude is a separate action", () => {
  let state = includeSite(defaultDiscoverView(), "f95");
  assert.equal(siteState(state, "f95"), "include");
  state = includeSite(state, "f95");
  assert.equal(siteState(state, "f95"), "neutral");
  assert.equal(activeDiscoverFilterCount(state), 0);
  state = excludeSite(includeSite(state, "f95"), "f95");
  assert.equal(siteState(state, "f95"), "exclude");
  assert.deepEqual(discoverQuery(state, "").include_sites, []);
  assert.deepEqual(discoverQuery(state, "").exclude_sites, ["f95"]);
  // A click on an excluded pill includes it; exclude again clears it.
  assert.equal(siteState(includeSite(state, "f95"), "f95"), "include");
  state = excludeSite(state, "f95");
  assert.equal(siteState(state, "f95"), "neutral");
  assert.equal(activeDiscoverFilterCount(state), 0);
});

test("tag pills compare without case and punctuation", () => {
  let state = includeTag(defaultDiscoverView(), "big tits");
  assert.equal(tagState(state, "Big-Tits"), "include");
  state = excludeTag(state, "BIG TITS");
  assert.deepEqual(state.includeTags, []);
  assert.deepEqual(state.excludeTags, ["BIG TITS"]);
  state = includeTag(state, "Big Tits");
  assert.deepEqual(state.includeTags, ["Big Tits"]);
  assert.deepEqual(state.excludeTags, []);
  assert.deepEqual(includeTag(state, "big-tits").includeTags, []);
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
  const state = { ...includeSite(defaultDiscoverView(), "lc"), sort: "title" as const, filtersOpen: false, hideLibrary: true };
  const cleared = clearDiscoverFilters(state);
  assert.equal(activeDiscoverFilterCount(cleared), 0);
  assert.equal(cleared.sort, "title");
  assert.equal(cleared.filtersOpen, false);
});

test("view choices round-trip through storage", () => {
  const storage = memoryStorage();
  const state = { ...toggleStatus(excludeTag(includeSite(defaultDiscoverView(), "dlsite"), "sandbox"), "onhold"), sort: "rating" as const, hideLibrary: true };
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

test("stored include and exclude lists from the old click cycle still load", () => {
  const state = normalizeDiscoverView({ version: 1, includeSites: ["lc"], excludeSites: ["f95"], includeTags: ["Sandbox"], excludeTags: ["NTR"] });
  assert.equal(siteState(state, "lc"), "include");
  assert.equal(siteState(state, "f95"), "exclude");
  assert.equal(tagState(state, "sandbox"), "include");
  assert.equal(tagState(state, "ntr"), "exclude");
});

test("one chip per active filter; removing a chip clears only that filter", () => {
  let state = excludeSite(includeSite(defaultDiscoverView(), "f95"), "lc");
  state = excludeTag(includeTag(toggleEngine(toggleStatus(state, "completed"), "Ren'Py"), "Sandbox"), "NTR");
  state = { ...state, minRating: "4.0", hideLibrary: true };
  const chips = discoverFilterChips(state);
  assert.equal(chips.length, activeDiscoverFilterCount(state));
  assert.deepEqual(chips.map((chip) => chip.label), ["F95Zone", "−LewdCorner", "Completed", "F95 rating 4.0+", "Ren'Py", "Sandbox", "−NTR", "Hide library games"]);
  assert.equal(new Set(chips.map((chip) => chip.key)).size, chips.length);
  const lc = chips.find((chip) => chip.label === "−LewdCorner");
  assert.ok(lc);
  assert.equal(lc.removeLabel, "Remove filter: excluded source LewdCorner");
  assert.equal(chips.at(-1)?.removeLabel, "Remove filter: Hide library games");
  assert.equal(chips.find((chip) => chip.kind === "rating")?.removeLabel, "Remove filter: F95 rating 4.0+");
  for (const chip of chips) {
    const next = removeDiscoverFilter(state, chip);
    assert.equal(activeDiscoverFilterCount(next), chips.length - 1, chip.key);
    assert.ok(!discoverFilterChips(next).some((other) => other.key === chip.key), chip.key);
  }
  const ntr = chips.find((chip) => chip.key === "tag:not:NTR");
  assert.ok(ntr);
  assert.deepEqual(removeDiscoverFilter({ ...state, excludeTags: ["ntr"] }, ntr).excludeTags, []);
});

test("the no-results text names the search and the active filters", () => {
  const chips = discoverFilterChips(excludeTag(includeSite(defaultDiscoverView(), "vndb"), "NTR"));
  assert.deepEqual(describeDiscoverNoResults("  echo ", chips), { title: "No games match “echo”", detail: "Filters on: VNDB, −NTR." });
  assert.deepEqual(describeDiscoverNoResults("", chips.slice(0, 1)), { title: "No games match your filters", detail: "Filter on: VNDB." });
  assert.equal(describeDiscoverNoResults("echo", []).detail, "Check the spelling, or search by original title or developer.");
  assert.deepEqual(describeDiscoverNoResults("", []), { title: "No games to show", detail: "" });
  assert.equal(describeDiscoverNoResults("", chips, 1).detail, "Filters on: VNDB and 1 more.");
  assert.match(describeDiscoverNoResults("x".repeat(80), []).title, /^No games match “x{47}…”$/);
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

test("the results area: cards, refresh, empty and error states", () => {
  const base = { loading: false, firstPage: false, error: "", errorOffset: 0, total: 240, shown: 120 };
  assert.deepEqual(discoverResultsView(base), { body: "cards", moreError: false, refreshing: false, searching: false });
  // A new query while cards show: they stay, dimmed.
  assert.equal(discoverResultsView({ ...base, loading: true, firstPage: true }).refreshing, true);
  // Loading the next page while scrolling is not a refresh.
  assert.equal(discoverResultsView({ ...base, loading: true, firstPage: false }).refreshing, false);
  // Nothing loaded yet: "Searching…", not the empty state.
  const first = discoverResultsView({ ...base, loading: true, firstPage: true, total: 0, shown: 0 });
  assert.equal(first.body, "cards");
  assert.equal(first.searching, true);
  assert.equal(first.refreshing, false);
  assert.equal(discoverResultsView({ ...base, total: 0, shown: 0 }).body, "empty");
  // A first-page error replaces the cards, also the old ones.
  assert.equal(discoverResultsView({ ...base, error: "Could not search the catalog" }).body, "error");
  assert.equal(discoverResultsView({ ...base, error: "x", total: 0, shown: 0 }).body, "error");
  // A next-page error keeps the loaded cards and goes under them.
  const more = discoverResultsView({ ...base, error: "x", errorOffset: 120 });
  assert.equal(more.body, "cards");
  assert.equal(more.moreError, true);
  assert.equal(discoverResultsView({ ...base, error: "x", errorOffset: 120, shown: 0 }).body, "error");
});

test("the card shows one source cluster and a status label", () => {
  assert.deepEqual(discoverSourceSummary(["f95", "lc", "vndb"]), { lead: "F95Zone", more: 2 });
  assert.deepEqual(discoverSourceSummary(["dlsite"]), { lead: "DLsite", more: 0 });
  assert.deepEqual(discoverSourceSummary([]), { lead: "", more: 0 });
  assert.deepEqual(discoverSiteNames(["lc", "ryuugames"]), ["LewdCorner", "Ryuugames"]);
  assert.equal(discoverStatusLabel("onhold"), "On hold");
  assert.equal(discoverStatusLabel(""), "");
});

test("pane sections fold, are saved, and survive Clear all", () => {
  const storage = memoryStorage();
  const folded = toggleDiscoverSection(toggleDiscoverSection(defaultDiscoverView(), "tags"), "status");
  assert.equal(folded.sections.tags, false);
  assert.equal(folded.sections.status, false);
  assert.equal(folded.sections.sources, true);
  writeDiscoverView(folded, storage);
  assert.deepEqual(readDiscoverView(storage).sections, folded.sections);
  assert.deepEqual(clearDiscoverFilters(includeTag(folded, "sandbox")).sections, folded.sections);
  // Old saved views without sections, and broken values, open every section.
  assert.deepEqual(normalizeDiscoverView({ version: 1 }).sections, defaultDiscoverView().sections);
  assert.deepEqual(
    normalizeDiscoverView({ version: 1, sections: { tags: false, rating: "no", nope: false } }).sections,
    { ...defaultDiscoverView().sections, tags: false },
  );
});

test("each section counts its own filters", () => {
  let state = excludeSite(includeSite(defaultDiscoverView(), "f95"), "lc");
  state = excludeTag(includeTag(state, "sandbox"), "ntr");
  state = { ...toggleEngine(toggleStatus(state, "completed"), "Unity"), minRating: "4.0", hideLibrary: true };
  assert.deepEqual(discoverSectionCounts(state), { sources: 2, status: 1, rating: 1, engines: 1, tags: 2 });
  assert.deepEqual(discoverSectionCounts(defaultDiscoverView()), { sources: 0, status: 0, rating: 0, engines: 0, tags: 0 });
});

test("the tag list shows the top tags, keeps chosen tags, and searches every tag", () => {
  const tags = Array.from({ length: 20 }, (_, index) => ({ key: `t${index}`, name: `Tag ${index}`, count: 100 - index }));
  const names = (list: { name: string }[]) => list.map((tag) => tag.name);
  const base = defaultDiscoverView();

  const preview = discoverTagList(tags, base, "", false);
  assert.deepEqual(names(preview.shown), names(tags.slice(0, 12)));
  assert.equal(preview.total, 20);
  assert.equal(preview.foldable, true);

  // A chosen tag past the top 12 stays visible, first in the list.
  const chosen = excludeTag(includeTag(base, "Tag 15"), "tag 18");
  const kept = discoverTagList(tags, chosen, "", false);
  assert.deepEqual(names(kept.shown), ["Tag 15", "Tag 18", ...names(tags.slice(0, 12))]);

  const all = discoverTagList(tags, base, "", true);
  assert.equal(all.shown.length, 20);
  assert.equal(all.foldable, true);

  // Search looks at every tag and shows every match; "Show all" does not apply.
  const found = discoverTagList(tags, base, " tag 1", false);
  assert.deepEqual(names(found.shown), ["Tag 1", ...Array.from({ length: 10 }, (_, index) => `Tag ${10 + index}`)]);
  assert.equal(found.foldable, false);

  // A short list has no "Show all".
  assert.equal(discoverTagList(tags.slice(0, 12), base, "", false).foldable, false);
});
