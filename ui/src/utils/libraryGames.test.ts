// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import {
  compareLibraryGames,
  gameFolder,
  gameTags,
  hasAvailableUpdate,
  hasNewSinceFinished,
  isWishlisted,
  matchesGameSearch,
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
