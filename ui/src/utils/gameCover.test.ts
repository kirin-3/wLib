// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { gameCoverSrc, gameCoverError, coverCardSources, coverAnimError } from "./gameCover.ts";

const fakeEvent = (src: string) => ({ currentTarget: { getAttribute: () => src } }) as unknown as Event;

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


test("cache precedes remote and follows local, with error fallback", () => {
  const game = { cover_local_url: "local-cache-priority.png", cover_cache_url: "cached-priority.png", cover_image_path: "remote-priority.png" };
  assert.equal(gameCoverSrc(game), game.cover_local_url);
  assert.equal(gameCoverError({} as Event, game), true);
  assert.equal(gameCoverSrc(game), game.cover_cache_url);
  assert.equal(gameCoverError({} as Event, game), true);
  assert.equal(gameCoverSrc(game), game.cover_image_path);
});

test("card sources use copies when present and fall back to the original", () => {
  const base = { cover_local_url: "cs-local.gif", cover_image_path: "cs-remote.gif" };
  assert.deepEqual(coverCardSources(base), { still: "", anim: "", original: "cs-local.gif", frame: false });
  assert.deepEqual(coverCardSources(null), { still: "", anim: "", original: "", frame: false });
  const both = { ...base, cover_card_still_url: "cs-a.still.webp", cover_card_anim_url: "cs-a.anim.webp" };
  assert.deepEqual(coverCardSources(both), { still: "cs-a.still.webp", anim: "cs-a.anim.webp", original: "cs-local.gif", frame: false });
  // Too long for an animated copy: the original plays in the animated layer.
  const stillOnly = { ...base, cover_card_still_url: "cs-b.still.webp" };
  assert.equal(coverCardSources(stillOnly).anim, "cs-local.gif");
  // An animated copy without its still copy is never used.
  assert.deepEqual(coverCardSources({ ...base, cover_card_anim_url: "cs-c.anim.webp" }).anim, "");
});

test("failed animated copy falls back to the original; failed still falls back to the original", () => {
  const game = { cover_local_url: "cf-local.gif", cover_card_still_url: "cf.still.webp", cover_card_anim_url: "cf.anim.webp" };
  coverAnimError(fakeEvent("cf.anim.webp"));
  assert.deepEqual(coverCardSources(game), { still: "cf.still.webp", anim: "cf-local.gif", original: "cf-local.gif", frame: false });
  assert.equal(gameCoverError(fakeEvent("cf.still.webp"), game), true);
  assert.deepEqual(coverCardSources(game), { still: "", anim: "", original: "cf-local.gif", frame: false });
  // An animated cover whose still copy failed draws its own still frame.
  assert.deepEqual(coverCardSources({ ...game, cover_animated: true }), { still: "", anim: "cf-local.gif", original: "cf-local.gif", frame: true });
});

test("animated cover without card copies draws a still frame and keeps the animated layer", () => {
  const game = { cover_local_url: "ca-local.webp", cover_animated: true };
  assert.deepEqual(coverCardSources(game), { still: "", anim: "ca-local.webp", original: "ca-local.webp", frame: true });
  assert.equal(coverCardSources({ cover_animated: true }).frame, false);
  assert.equal(coverCardSources({ ...game, cover_card_still_url: "ca.still.webp" }).frame, false);
});
