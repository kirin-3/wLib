// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { lcActive, normalizeLcThreadUrl, type LewdCornerMode } from "./lewdcorner.ts";

const url = "https://lewdcorner.com/threads/123/";

test("normalizes LC thread URL variants and rejects other pages", () => {
  for (const input of [url, "www.lewdcorner.com/threads/slug.123/post-4#post-4", "http://lewdcorner.com/threads/slug.123/page-2?b=1"]) {
    assert.equal(normalizeLcThreadUrl(input), url);
  }
  for (const input of ["https://lewdcorner.com/forums/games.6/", "https://other.com/threads/123/", "/threads/123/", "https://lewdcorner.com.evil/threads/123/"]) {
    assert.equal(normalizeLcThreadUrl(input), "");
  }
});

test("activation requires an LC thread and respects the game override", () => {
  const f95 = "https://f95zone.to/threads/456/";
  const cases: [LewdCornerMode, boolean, string, boolean][] = [
    ["", false, f95, false], ["", true, f95, true], ["", false, "", true],
    ["on", false, f95, true], ["off", true, "", false],
  ];
  for (const [mode, enabled, thread, expected] of cases) {
    const game = { lc_url: url, lc_mode: mode, f95_url: thread };
    assert.equal(lcActive(game, enabled), expected);
    assert.equal(lcActive({ ...game, lc_url: "" }, enabled), false);
  }
});
