// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { gameCoverSrc, gameCoverError } from "./gameCover.ts";

test("local cover wins, errors fall back once, replacement is tried afresh", () => {
  const game = { cover_local_url: "http://127.0.0.1:8183/covers/test.png", cover_image_path: "https://example.com/test.jpg" };
  assert.equal(gameCoverSrc(game), game.cover_local_url);
  assert.equal(gameCoverError({} as Event, game), true);
  assert.equal(gameCoverSrc(game), game.cover_image_path);
  assert.equal(gameCoverError({} as Event, game), false);
  assert.equal(gameCoverSrc(game), "");
  assert.equal(gameCoverSrc({ ...game, cover_local_url: "new.png" }), "new.png");
});

test("missing local cover uses remote; empty game uses placeholder", () => {
  assert.equal(gameCoverSrc({ cover_image_path: "remote.png" }), "remote.png");
  assert.equal(gameCoverSrc(null), "");
  assert.equal(gameCoverSrc({}), "");
});
