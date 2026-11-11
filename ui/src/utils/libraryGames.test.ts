// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildTagFilterOptions,
  formatLastPlayed,
  formatPlaytime,
  defaultTargetLabel,
  getEffectiveLaunchTargets,
  hasAdditionalLaunchTargets,
  compareLibraryGames,
  gameFolder,
  gameSources,
  matchesSourceFilter,
  gameTags,
  hasAvailableUpdate,
  hasTagFilter,
  hasUnplayedUpdate,
  isUpdateSkipped,
  hasNewSinceFinished,
  isWishlisted,
  matchesGameSearch,
  matchesTagFilter,
  normalizeVersion,
  toggleTagFilter,
} from "./libraryGames.ts";
import type { GameRecord } from "../services/api.ts";

test("updates ignore whitespace, include unknown installed versions, and disappear after marking", () => {
  assert.equal(
    hasAvailableUpdate({ version: "1.0", latest_version: " 1.0 " }),
    false,
  );
  assert.equal(
    hasAvailableUpdate({ version: "1.0", latest_version: " " }),
    false,
  );
  assert.equal(hasAvailableUpdate({ latest_version: "2.0" }), true);
  assert.equal(
    hasAvailableUpdate({ version: "v0.8", latest_version: "0.8 " }),
    false,
  );
  assert.equal(
    hasAvailableUpdate({
      version: "Ch. 2 Final",
      latest_version: "ch.2 final",
    }),
    false,
  );
  assert.equal(
    hasAvailableUpdate({ version: "v0.08", latest_version: "v0.8" }),
    true,
  );
  const game = { version: "1.0", latest_version: "2.0" };
  assert.equal(hasAvailableUpdate(game), true);
  assert.equal(
    hasAvailableUpdate({ ...game, version: game.latest_version }),
    false,
  );
});

test("games without an executable are wishlisted and have no update indicator", () => {
  assert.equal(isWishlisted({ exe_path: "  " }), true);
  assert.equal(isWishlisted({ exe_path: "/games/demo.sh" }), false);
  assert.equal(
    hasAvailableUpdate({ exe_path: "", version: "1.0", latest_version: "2.0" }),
    false,
  );
  assert.equal(
    hasAvailableUpdate({
      exe_path: "/games/demo.sh",
      version: "1.0",
      latest_version: "2.0",
    }),
    true,
  );
});

test("new since finished matches any normalized difference and skips unfinished games", () => {
  assert.equal(
    hasNewSinceFinished({ version: "0.7", finished_version: "0.5" }),
    true,
  );
  assert.equal(
    hasNewSinceFinished({ version: "0.7", finished_version: "v0.7" }),
    false,
  );
  assert.equal(
    hasNewSinceFinished({ version: "0.7", finished_version: "" }),
    false,
  );
  assert.equal(hasNewSinceFinished({ version: "0.7" }), false);
  assert.equal(
    hasNewSinceFinished({ version: " 0.7 ", finished_version: " V 0.7 " }),
    false,
  );
  // "Differs" includes downgrades; versions are not ordered.
  assert.equal(
    hasNewSinceFinished({ version: "0.5", finished_version: "0.7" }),
    true,
  );
});

test("search includes title, developer, engine and normalized tags in both storage formats", () => {
  const game: GameRecord = {
    id: 1,
    title: "A Game",
    exe_path: "/game/run",
    developer: "Studio",
    engine: "Ren'Py",
    tags: " fantasy, sci-fi, fantasy, ",
  };
  for (const query of ["a game", "STUDIO", " ren'py ", "sci-fi"])
    assert.equal(matchesGameSearch(game, query), true);
  assert.equal(matchesGameSearch(game, "Unity"), false);
  assert.deepEqual(gameTags(game), ["fantasy", "sci-fi"]);
  assert.deepEqual(gameTags({ tags: [" fantasy ", "", "fantasy", "sci-fi"] }), [
    "fantasy",
    "sci-fi",
  ]);
});

test("tag filters treat names case-insensitively and merge casing variants", () => {
  const games = [
    { tags: "Sandbox, RPG" },
    { tags: "sandbox, rpg" },
    { tags: "Other" },
  ];
  const options = buildTagFilterOptions(games);
  assert.deepEqual(options, [
    { label: "Other", count: 1 },
    { label: "RPG", count: 2 },
    { label: "Sandbox", count: 2 },
  ]);
  // One game carrying both spellings still counts once.
  assert.deepEqual(buildTagFilterOptions([{ tags: "Sandbox, sandbox" }]), [
    { label: "Sandbox", count: 1 },
  ]);

  assert.equal(matchesTagFilter({ tags: "Sandbox" }, ["sandbox"]), true);
  assert.equal(matchesTagFilter({ tags: "sandbox" }, ["Sandbox"]), true);
  assert.equal(matchesTagFilter({ tags: "Sandbox" }, ["Sandbox", "rpg"]), false);
  assert.equal(matchesTagFilter({ tags: "Sandbox, RPG" }, ["sandbox", "rpg"]), true);
  assert.equal(matchesTagFilter({ tags: "Sandbox" }, []), true);

  assert.equal(hasTagFilter(["sandbox"], "Sandbox"), true);
  assert.equal(hasTagFilter(["Sandbox"], "sandbox"), true);
  assert.equal(hasTagFilter(["Other"], "Sandbox"), false);
  assert.deepEqual(toggleTagFilter(["sandbox", "Other"], "Sandbox", false), ["Other"]);
  assert.deepEqual(toggleTagFilter(["sandbox"], "Sandbox", true), ["Sandbox"]);
  assert.deepEqual(toggleTagFilter([], "Sandbox", true), ["Sandbox"]);
});

test("versions normalize the same way the Python mirror does, including non-strings", () => {
  // normalize_version() in core/versions.py coerces with str(value or ""); the UI must
  // not throw on a version that arrived as a number or array, or a single bad
  // row breaks the whole library (updateCount and the sort read every game).
  assert.equal(normalizeVersion("v0.8"), "0.8");
  assert.equal(normalizeVersion("  V 0.8 "), "0.8");
  assert.equal(normalizeVersion(1 as unknown as string), "1");
  assert.equal(normalizeVersion(0 as unknown as string), "");
  assert.equal(normalizeVersion(["1.0"] as unknown as string), "1.0");
  assert.doesNotThrow(() => normalizeVersion({} as unknown as string));
  assert.equal(normalizeVersion(null), "");
  assert.equal(normalizeVersion(undefined), "");

  assert.equal(hasAvailableUpdate({ version: 1 as unknown as string, latest_version: "2.0" }), true);
  assert.equal(
    hasAvailableUpdate({ version: "1.0", latest_version: 1 as unknown as string }),
    true,
  );
  assert.equal(
    hasAvailableUpdate({ version: 1 as unknown as string, latest_version: 1 as unknown as string }),
    false,
  );
  assert.equal(hasNewSinceFinished({ version: 1 as unknown as string, finished_version: "0.5" }), true);
});

test("table sorts versions naturally, statuses through normalization, and recent dates with missing values last", () => {
  const a: GameRecord = {
    id: 1,
    title: "A",
    exe_path: "",
    version: "1.9",
    status: "completed",
    last_played: "2026-10-01",
    playtime_seconds: 60,
  };
  const b: GameRecord = {
    id: 2,
    title: "B",
    exe_path: "",
    version: "1.10",
    play_status: "Playing",
    last_played: "2026-10-03",
    playtime_seconds: 120,
  };
  for (const field of [
    "title",
    "version",
    "play_status",
    "last_played",
    "playtime_seconds",
  ] as const) {
    assert.ok(compareLibraryGames(a, b, field, "asc") < 0);
    assert.ok(compareLibraryGames(a, b, field, "desc") > 0);
  }
  assert.ok(
    compareLibraryGames(
      b,
      { id: 3, title: "C", exe_path: "" },
      "last_played",
      "desc",
    ) < 0,
  );
  assert.ok(
    compareLibraryGames(
      { ...a, engine: "Ren'Py" },
      { ...b, engine: "Unity" },
      "engine",
      "asc",
    ) < 0,
  );
  const unplayed: GameRecord = { id: 4, title: "D", exe_path: "" };
  for (const direction of ["asc", "desc"] as const) {
    assert.ok(compareLibraryGames(unplayed, a, "last_played", direction) > 0);
    assert.ok(
      compareLibraryGames(a, unplayed, "playtime_seconds", direction) < 0,
    );
    assert.ok(
      compareLibraryGames({ ...a, title: "Z" }, a, "engine", direction) > 0,
    );
  }
});

test("version sorting treats a leading v as the same version", () => {
  const prefixed: GameRecord = { id: 1, title: "Prefixed", exe_path: "", version: "v0.9" };
  const plain: GameRecord = { id: 2, title: "Plain", exe_path: "", version: "0.10" };
  // 0.9 < 0.10 under the same natural-number rule the version column already
  // uses; the "v" prefix must not flip the two.
  assert.ok(compareLibraryGames(prefixed, plain, "version", "asc") < 0);
  assert.ok(compareLibraryGames(prefixed, plain, "version", "desc") > 0);
  const same: GameRecord = {
    id: 9,
    title: "Same",
    exe_path: "",
    version: "v0.10",
  };
  assert.equal(
    compareLibraryGames(same, { ...same, version: "V 0.10 " }, "version", "asc"),
    0,
  );
});

test("folder actions preserve POSIX and Windows roots and reject bare commands", () => {
  assert.equal(gameFolder("/games/title/run.sh"), "/games/title/");
  assert.equal(gameFolder("/run.sh"), "/");
  assert.equal(gameFolder("C:\\Games\\title\\Game.exe"), "C:\\Games\\title\\");
  assert.equal(gameFolder("C:\\Game.exe"), "C:\\");
  assert.equal(gameFolder("game"), "");
});

test("search matches unicode accents and decomposed characters case-insensitively", () => {
  const game: GameRecord = {
    id: 1,
    title: "Pok\u00e9mon Mystery Café", // precomposed é
    exe_path: "/game/run",
    developer: "Développeur",
    engine: "Unity",
    tags: ["Aventure", "Café"],
  };
  // Search without accents
  assert.equal(matchesGameSearch(game, "pokemon"), true);
  assert.equal(matchesGameSearch(game, "cafe"), true);
  assert.equal(matchesGameSearch(game, "developpeur"), true);
  // Search with decomposed accents (e + combining acute)
  assert.equal(matchesGameSearch(game, "Poke\u0301mon"), true);
  assert.equal(matchesGameSearch(game, "Cafe\u0301"), true);
  // Search with uppercase and whitespace
  assert.equal(matchesGameSearch(game, "  POKEMON  "), true);
  // Non-matching query
  assert.equal(matchesGameSearch(game, "digimon"), false);
});

test("table sort breaks ties stably by ID and sinks missing titles", () => {
  const g1: GameRecord = { id: 10, title: "Echoes", exe_path: "", rating: "4.5" };
  const g2: GameRecord = { id: 2, title: "Echoes", exe_path: "", rating: "4.5" };
  // Same field, same title: tie broken by ID
  assert.ok(compareLibraryGames(g1, g2, "rating", "asc") > 0);
  assert.ok(compareLibraryGames(g2, g1, "rating", "asc") < 0);
  // Same title when sorting by title: tie broken by ID
  assert.ok(compareLibraryGames(g1, g2, "title", "asc") > 0);
  assert.ok(compareLibraryGames(g1, g2, "title", "desc") > 0);

  // Missing title sinks to the bottom when tie-breaking
  const gNoTitle: GameRecord = { id: 3, title: "", exe_path: "", rating: "4.5" };
  assert.ok(compareLibraryGames(gNoTitle, g1, "rating", "asc") > 0);
  assert.ok(compareLibraryGames(g1, gNoTitle, "rating", "asc") < 0);

  // Non-string version number does not crash
  const gNumericVersion: GameRecord = { id: 4, title: "NumVer", exe_path: "", version: 1 as unknown as string };
  assert.doesNotThrow(() => compareLibraryGames(gNumericVersion, g1, "version", "asc"));
});

test("unplayed updates require an installed game and two different known versions", () => {
  const base = { exe_path: "/game", version: "0.8", last_played_version: "0.7" };
  assert.equal(hasUnplayedUpdate(base), true);
  assert.equal(hasUnplayedUpdate({ ...base, last_played_version: " V 0.8 " }), false);
  assert.equal(hasUnplayedUpdate({ ...base, last_played_version: "" }), false);
  assert.equal(hasUnplayedUpdate({ ...base, version: "" }), false);
  assert.equal(hasUnplayedUpdate({ ...base, exe_path: "  " }), false);
});

test("skipping hides exactly one normalized release", () => {
  const game = { exe_path: "/game", version: "0.7", latest_version: "0.8", skipped_version: " V 0.8 " };
  assert.equal(isUpdateSkipped(game), true);
  assert.equal(hasAvailableUpdate(game), false);
  assert.equal(hasAvailableUpdate({ ...game, latest_version: "0.9" }), true);
  assert.equal(isUpdateSkipped({ ...game, latest_version: "0.9" }), false);
  assert.equal(isUpdateSkipped({ ...game, skipped_version: "" }), false);
  assert.equal(isUpdateSkipped({ ...game, exe_path: "" }), false);
});


test("library display state waits for the first load even with restored filters", async () => {
  const { libraryDisplayState } = await import("./libraryGames.ts");
  assert.equal(libraryDisplayState(false, null, 0, 0), "loading");
  assert.equal(libraryDisplayState(false, null, 25, 0), "loading");
  assert.equal(libraryDisplayState(false, "Failed", 0, 0), "error");
  assert.equal(libraryDisplayState(true, null, 0, 0), "empty");
  assert.equal(libraryDisplayState(true, null, 25, 0), "no-results");
  assert.equal(libraryDisplayState(true, null, 25, 25), "list");
  assert.equal(libraryDisplayState(true, "Refresh failed", 25, 25), "list");
});


test("game sources derive from F95 and supported store links", () => {
  const link = (source: string) => ({ source, id: "1", url: "https://example.com" });
  assert.deepEqual([...gameSources({ f95_url: " https://f95zone.to/thread ", links: [link("dlsite")] })], ["f95", "dlsite"]);
  assert.deepEqual([...gameSources({ links: [link("ryuugames"), link("dlsite"), link("vndb")] })], ["ryuugames", "dlsite", "vndb"]);
  assert.equal(gameSources({ f95_url: "   " }).size, 0);
  assert.equal(gameSources({ links: [link("patreon"), link("steam")] }).size, 0);
});

test("source filters match any selected source or all games when empty", () => {
  const game = { f95_url: "thread", links: [{ source: "dlsite", id: "1", url: "url" }] };
  assert.equal(matchesSourceFilter(game, []), true);
  assert.equal(matchesSourceFilter(game, ["dlsite"]), true);
  assert.equal(matchesSourceFilter(game, ["vndb", "f95"]), true);
  assert.equal(matchesSourceFilter(game, ["vndb", "ryuugames"]), false);
});

test("playtime and last-played formatting", () => {
  assert.equal(formatPlaytime(null), "0.0 hrs");
  assert.equal(formatPlaytime(5400), "1.5 hrs");
  assert.equal(formatLastPlayed(null), "Never");
  assert.equal(formatLastPlayed("not a date"), "Never");
  assert.notEqual(formatLastPlayed("2026-01-02T00:00:00Z"), "Never");
});

test("launch targets list the default first, then by sort order", () => {
  const targets = getEffectiveLaunchTargets({
    id: 7,
    exe_path: "/g/a.sh",
    launch_targets: [
      { id: 2, game_id: 7, label: "B", exe_path: "/g/b", sort_order: 1 },
      { id: 1, game_id: 7, label: "A", exe_path: "/g/a2", sort_order: 1 },
      { id: 3, game_id: 7, label: "C", exe_path: "/g/c", sort_order: 0 },
    ],
  } as never);
  assert.deepEqual(targets.map((t) => t.id), ["default-7", "target-3", "target-1", "target-2"]);
  assert.equal(targets[0].isDefault, true);
});

test("additional launch targets are hidden while running or wishlisted", () => {
  const target = { id: 1, game_id: 1, label: "A", exe_path: "/a", sort_order: 0 };
  const game = { exe_path: "/g/a.sh", launch_targets: [target] } as never;
  assert.equal(hasAdditionalLaunchTargets(game, false), true);
  assert.equal(hasAdditionalLaunchTargets(game, true), false);
  assert.equal(hasAdditionalLaunchTargets({ exe_path: "", launch_targets: [target] } as never, false), false);
  assert.equal(hasAdditionalLaunchTargets({ exe_path: "/g/a.sh", launch_targets: [] } as never, false), false);
});

test("defaultTargetLabel falls back to Default for empty, blank or missing names", () => {
  assert.equal(defaultTargetLabel(undefined), "Default");
  assert.equal(defaultTargetLabel({}), "Default");
  assert.equal(defaultTargetLabel({ default_target_label: "   " }), "Default");
});

test("defaultTargetLabel returns the trimmed name", () => {
  assert.equal(defaultTargetLabel({ default_target_label: " Season 1 " }), "Season 1");
});

test("getEffectiveLaunchTargets names the default entry", () => {
  const targets = getEffectiveLaunchTargets({
    id: 4,
    exe_path: "/g/s1.sh",
    default_target_label: "Season 1",
    launch_targets: [],
  });
  assert.equal(targets[0]?.label, "Season 1");
  assert.equal(targets[0]?.isDefault, true);
});
