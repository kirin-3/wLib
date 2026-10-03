// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import {
  compareLibraryGames,
  gameFolder,
  gameTags,
  hasAvailableUpdate,
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
  const game = { version: "1.0", latest_version: "2.0" };
  assert.equal(hasAvailableUpdate(game), true);
  assert.equal(
    hasAvailableUpdate({ ...game, version: game.latest_version }),
    false,
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
});

test("folder actions preserve POSIX and Windows roots and reject bare commands", () => {
  assert.equal(gameFolder("/games/title/run.sh"), "/games/title/");
  assert.equal(gameFolder("/run.sh"), "/");
  assert.equal(gameFolder("C:\\Games\\title\\Game.exe"), "C:\\Games\\title\\");
  assert.equal(gameFolder("C:\\Game.exe"), "C:\\");
  assert.equal(gameFolder("game"), "");
});
