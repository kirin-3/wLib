// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_FILTER_SECTIONS,
  LEGACY_FILTERS_PANE_STORAGE_KEY,
  LEGACY_FILTER_SECTIONS_STORAGE_KEY,
  LEGACY_LAYOUT_MODE_STORAGE_KEY,
  LIBRARY_PLAY_STATUSES,
  LIBRARY_VIEW_STATE_STORAGE_KEY,
  clearLegacyLibraryViewState,
  hasStatusFilter,
  normalizeFilterAndSort,
  normalizeFilterPreset,
  normalizeLibraryViewState,
  readLibraryViewState,
  readPresetsForSave,
  saveLibraryViewState,
  toggleStatusFilter,
  type LibraryFilterPreset,
  type LibraryViewState,
} from "./libraryViewState.ts";

class MemoryStorage implements Storage {
  private readonly store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
}

test("new collections and sortable table columns survive persistence", () => {
  const storage = new MemoryStorage();
  for (const filterCollection of [
    "Updates available",
    "New since finished",
    "Unplayed update",
    "Recently played",
  ]) {
    for (const sortBy of ["version", "play_status", "engine"]) {
      saveLibraryViewState(storage, normalizeLibraryViewState({ filterCollection, sortBy, layoutMode: "list" }));
      const { state } = readLibraryViewState(storage);
      assert.equal(state.filterCollection, filterCollection);
      assert.equal(state.sortBy, sortBy);
      assert.equal(state.layoutMode, "list");
    }
  }
});

test("banner layout survives persistence", () => {
  const storage = new MemoryStorage();
  saveLibraryViewState(storage, normalizeLibraryViewState({ layoutMode: "banner" }));
  assert.equal(readLibraryViewState(storage).state.layoutMode, "banner");
});

test("banner layout is accepted from the legacy layout key", () => {
  const storage = new MemoryStorage();
  storage.setItem(LEGACY_LAYOUT_MODE_STORAGE_KEY, "banner");
  const restored = readLibraryViewState(storage);
  assert.equal(restored.source, "legacy");
  assert.equal(restored.state.layoutMode, "banner");
});

test("install state filter survives persistence", () => {
  const storage = new MemoryStorage();
  saveLibraryViewState(storage, normalizeLibraryViewState({ filterInstallState: "wishlist" }));
  assert.equal(readLibraryViewState(storage).state.filterInstallState, "wishlist");
  assert.equal(normalizeLibraryViewState({ filterInstallState: "invalid" }).filterInstallState, "");
});

test("normalizeLibraryViewState falls back on invalid static values", () => {
  const state = normalizeLibraryViewState({
    layoutMode: "poster",
    sortBy: "developer",
    sortDir: "sideways",
    filterCollection: "FavoritesOnly",
    filterStatuses: ["Playing", "Unknown", "Playing", 7],
    filterEngines: ["  Ren'Py  ", "", "Unity", "Unity", 4],
    filterTags: ["  sci-fi  ", "", "tagged", 4],
    isFiltersCollapsed: "sometimes",
    filterSections: {
      collections: false,
      status: "yes",
      engines: "no",
    },
  });

  assert.equal(state.layoutMode, "grid");
  assert.equal(state.sortBy, "title");
  assert.equal(state.sortDir, "asc");
  assert.equal(state.filterCollection, "All");
  assert.deepEqual(state.filterStatuses, ["Playing", "Unknown"]);
  assert.deepEqual(state.filterEngines, ["Ren'Py", "Unity"]);
  assert.deepEqual(state.filterTags, ["sci-fi", "tagged"]);
  assert.equal(state.isFiltersCollapsed, true);
  assert.deepEqual(state.filterSections, {
    ...DEFAULT_FILTER_SECTIONS,
    collections: false,
  });});

test("readLibraryViewState migrates legacy keys when no blob exists", () => {
  const storage = new MemoryStorage();
  storage.setItem(LEGACY_LAYOUT_MODE_STORAGE_KEY, "compact");
  storage.setItem(LEGACY_FILTERS_PANE_STORAGE_KEY, "false");
  storage.setItem(
    LEGACY_FILTER_SECTIONS_STORAGE_KEY,
    JSON.stringify({ collections: false, status: true, tags: false }),
  );

  const restored = readLibraryViewState(storage);

  assert.equal(restored.source, "legacy");
  assert.equal(restored.state.layoutMode, "compact");
  assert.equal(restored.state.isFiltersCollapsed, false);
  assert.deepEqual(restored.state.filterSections, {
    presets: true,
    collections: false,
    status: true,
    engines: true,
    tags: false,
  });

  saveLibraryViewState(storage, restored.state);
  clearLegacyLibraryViewState(storage);

  assert.equal(storage.getItem(LEGACY_LAYOUT_MODE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTERS_PANE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTER_SECTIONS_STORAGE_KEY), null);
  assert.notEqual(storage.getItem(LIBRARY_VIEW_STATE_STORAGE_KEY), null);
});

test("readLibraryViewState prefers current blob and prunes unsupported engines and tags", () => {
  const storage = new MemoryStorage();
  const state: LibraryViewState = {
    version: 1,
    layoutMode: "list",
    cardSize: 2,
    sortBy: "rating",
    sortDir: "desc",
    filterCollection: "Favorites",
    filterStatuses: ["Completed"],
    filterEngines: ["keep-engine", "drop-engine"],
    filterTags: ["keep", "drop"],
    isFiltersCollapsed: false,
    filterSections: { collections: true, status: false, engines: false, tags: true },
    activeCollection: "Backlog",
  };

  saveLibraryViewState(storage, state);
  const restored = readLibraryViewState(storage, {
    validEngines: ["keep-engine"],
    validTags: ["keep"],
    validCollections: ["Backlog"],
  });

  assert.equal(restored.source, "current");
  assert.equal(restored.state.layoutMode, "list");
  assert.equal(restored.state.sortBy, "rating");
  assert.equal(restored.state.filterCollection, "Favorites");
  assert.deepEqual(restored.state.filterStatuses, ["Completed"]);
  assert.deepEqual(restored.state.filterEngines, ["keep-engine"]);
  assert.deepEqual(restored.state.filterTags, ["keep"]);
  assert.equal(restored.state.activeCollection, "Backlog");
  assert.deepEqual(restored.state.filterSections, {
    presets: true,
    collections: true,
    status: false,
    engines: false,
    tags: true,
  });
});

test("active collection restores, falls back to All, and defaults for legacy state", () => {
  const storage = new MemoryStorage();
  saveLibraryViewState(storage, normalizeLibraryViewState({ activeCollection: "Backlog" }));
  assert.equal(readLibraryViewState(storage).state.activeCollection, "Backlog");

  // Unknown names (deleted, renamed, or synced away) fall back to "" = All.
  const pruned = readLibraryViewState(storage, { validCollections: ["Best of"] });
  assert.equal(pruned.state.activeCollection, "");

  // Matching is case-insensitive and keeps the stored spelling.
  const recased = readLibraryViewState(storage, { validCollections: ["backlog"] });
  assert.equal(recased.state.activeCollection, "backlog");

  // Legacy blobs without the field restore as "" without failing.
  storage.setItem(
    LIBRARY_VIEW_STATE_STORAGE_KEY,
    JSON.stringify({ version: 1, layoutMode: "grid" }),
  );
  assert.equal(readLibraryViewState(storage).state.activeCollection, "");
  assert.equal(normalizeLibraryViewState({ activeCollection: 7 }).activeCollection, "");
  assert.equal(normalizeLibraryViewState({ activeCollection: "  " }).activeCollection, "");
});

test("normalizeFilterPreset keeps a valid preset and drops unknown values", () => {
  const valid = normalizeFilterPreset(
    {
      name: " Ren'Py now ",
      filterCollection: "Favorites",
      filterStatuses: ["Playing"],
      filterInstallState: "installed",
      filterEngines: ["Ren'Py"],
      filterTags: ["dating-sim"],
      sortBy: "rating",
      sortDir: "desc",
    },
  );
  assert.deepEqual(valid, {
    name: "Ren'Py now",
    filterCollection: "Favorites",
    filterStatuses: ["Playing"],
    filterInstallState: "installed",
    filterEngines: ["Ren'Py"],
    filterTags: ["dating-sim"],
    sortBy: "rating",
    sortDir: "desc",
  });

  // Unknown engines, tags and enum values are dropped on apply.
  const pruned = normalizeFilterPreset(
    {
      name: "Pruned",
      filterCollection: "FavoritesOnly",
      filterStatuses: ["Playing", "Ghost"],
      filterInstallState: "bogus",
      filterEngines: ["Ren'Py", "Ghost"],
      filterTags: ["dating-sim", "ghost-tag"],
      sortBy: "developer",
      sortDir: "sideways",
    },
    { validEngines: ["Ren'Py"], validTags: ["dating-sim"] },
  );
  assert.deepEqual(pruned, {
    name: "Pruned",
    filterCollection: "All",
    filterStatuses: ["Playing", "Ghost"],
    filterInstallState: "",
    filterEngines: ["Ren'Py"],
    filterTags: ["dating-sim"],
    sortBy: "title",
    sortDir: "asc",
  });

  // Entries without a usable name are refused.
  for (const bad of [null, {}, { name: "" }, { name: "   " }, { name: "x".repeat(41) }, "corrupt"]) {
    assert.equal(normalizeFilterPreset(bad), null);
  }
});

test("normalizeLibraryViewState preserves the expanded supported status set", () => {
  const state = normalizeLibraryViewState({
    filterStatuses: [...LIBRARY_PLAY_STATUSES, "Unknown"],
  });

  assert.deepEqual(state.filterStatuses, [...LIBRARY_PLAY_STATUSES, "Unknown"]);
});

test("unknown statuses are pruned only once the status list is known", () => {
  // Without a list nothing is dropped: restoreLibraryViewState() reads the blob
  // before Settings has loaded, so a real status must survive that window.
  assert.deepEqual(normalizeFilterAndSort({ filterStatuses: ["Playing", "Backlog"] }).filterStatuses, [
    "Playing",
    "Backlog",
  ]);

  // With the live list (custom status renamed or deleted, preset from another
  // device) the stale name goes instead of silently matching nothing. Spelling
  // and case are kept; only exact duplicates collapse.
  assert.deepEqual(
    normalizeFilterAndSort(
      { filterStatuses: ["playing", "Backlog", "Ghost", "Playing", "Backlog"] },
      { validStatuses: ["Playing", "Completed", "Backlog"] },
    ).filterStatuses,
    ["playing", "Backlog", "Playing"],
  );
  assert.deepEqual(
    normalizeFilterAndSort({ filterStatuses: ["Backlog"] }, { validStatuses: [] }).filterStatuses,
    [],
  );
  assert.deepEqual(
    normalizeFilterPreset(
      { name: "Ghost free", filterStatuses: ["Backlog", "Ghost"] },
      { validStatuses: ["Backlog"] },
    )?.filterStatuses,
    ["Backlog"],
  );

  // Case-only differences survive because the status filter itself matches
  // case-insensitively, and the stored spelling is kept.
  assert.deepEqual(
    normalizeFilterAndSort(
      { filterStatuses: ["playing"] },
      { validStatuses: ["Playing"] },
    ).filterStatuses,
    ["playing"],
  );

  // Names past the 40-character status cap can never match and are always dropped.
  assert.deepEqual(
    normalizeFilterAndSort({ filterStatuses: ["Playing", "x".repeat(41)] }).filterStatuses,
    ["Playing"],
  );

  // A non-iterable status list behaves like no list at all.
  assert.doesNotThrow(() =>
    normalizeFilterAndSort(
      { filterStatuses: ["Playing"] },
      { validStatuses: 42 as unknown as Iterable<string> },
    ),
  );
});

test("normalizeActiveCollection and filterAllowedValues handle non-string and non-iterable collections safely", () => {
  // Collections containing null, undefined or numbers do not crash
  const mixedCollections = ["RPG", null as unknown as string, undefined as unknown as string, 42 as unknown as string];
  assert.equal(
    readLibraryViewState(new MemoryStorage(), { validCollections: mixedCollections }).state.activeCollection,
    "",
  );
  // Non-iterable objects do not crash
  assert.doesNotThrow(() => {
    readLibraryViewState(new MemoryStorage(), {
      validCollections: {} as unknown as Iterable<string>,
      validEngines: 123 as unknown as Iterable<string>,
      validTags: null as unknown as Iterable<string>,
    });
  });
});


test("card size round trips without entering filter presets", () => {
  const storage = new MemoryStorage();
  const state = normalizeLibraryViewState({ cardSize: 4, layoutMode: "banner", sortBy: "rating" });
  saveLibraryViewState(storage, state);
  assert.equal(readLibraryViewState(storage).state.cardSize, 4);
  assert.equal("cardSize" in normalizeFilterAndSort(state), false);
  assert.equal("cardSize" in normalizeFilterPreset({ ...state, name: "Favorites" })!, false);
});

test("missing or invalid card sizes retain the other preferences", () => {
  for (const cardSize of [undefined, -1, 5, 2.5, "3"]) {
    const state = normalizeLibraryViewState({ cardSize, layoutMode: "compact", sortBy: "rating", activeCollection: "Backlog" });
    assert.equal(state.cardSize, 2);
    assert.equal(state.layoutMode, "compact");
    assert.equal(state.sortBy, "rating");
    assert.equal(state.activeCollection, "Backlog");
  }
});

test("status filters match and toggle case-insensitively", () => {
  // A restored preset keeps its spelling; the sidebar option uses the canonical one.
  assert.equal(hasStatusFilter(["playing"], "Playing"), true);
  assert.equal(hasStatusFilter(["Playing", "Backlog"], "Backlog"), true);
  assert.equal(hasStatusFilter(["Completed"], "Playing"), false);
  assert.equal(hasStatusFilter([], "Playing"), false);

  // Unchecking removes every spelling so the filter cannot stay applied with an
  // unchecked box; checking replaces the stale spelling with the visible one.
  assert.deepEqual(toggleStatusFilter(["playing", "Playing"], "Playing", false), []);
  assert.deepEqual(toggleStatusFilter(["playing"], "Playing", true), ["Playing"]);
  assert.deepEqual(toggleStatusFilter(["Completed"], "Playing", true), ["Completed", "Playing"]);
  assert.deepEqual(toggleStatusFilter(["Completed"], "Playing", false), ["Completed"]);
  // Toggling a spelling that was not selected only affects the matching name.
  assert.deepEqual(toggleStatusFilter(["backlog", "BACKLOG", "Playing"], "Backlog", false), ["Playing"]);
});

test("saving presets reloads the stored list when settings have not loaded", async () => {
  const stored: LibraryFilterPreset[] = [
    {
      name: "Existing",
      filterCollection: "All",
      filterStatuses: [],
      filterInstallState: "",
      filterEngines: [],
      filterTags: [],
      sortBy: "title",
      sortDir: "asc",
    },
  ];
  let reads = 0;

  // An unloaded view must read the stored list before replacing it.
  const fetched = await readPresetsForSave(
    async () => {
      reads += 1;
      return { library_filter_presets: stored };
    },
    [],
    false,
  );
  assert.deepEqual(fetched, { presets: stored, loaded: true });
  assert.equal(reads, 1);

  // Once loaded, sharing the current list needs no extra read.
  const cached = await readPresetsForSave(
    async () => {
      reads += 1;
      return { library_filter_presets: [] };
    },
    stored,
    true,
  );
  assert.deepEqual(cached, { presets: stored, loaded: true });
  assert.equal(reads, 1);

  // A failed read must not report an empty list as loaded.
  const failed = await readPresetsForSave(
    async () => {
      throw new Error("database is locked");
    },
    stored,
    false,
  );
  assert.deepEqual(failed, { presets: stored, loaded: false });
});
