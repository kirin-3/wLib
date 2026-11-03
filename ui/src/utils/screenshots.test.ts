// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { screenshotUrls } from "./screenshots.ts";

test("maps either F95 media host and preserves other URLs", () => {
    const pair = { thumb: "https://preview.f95zone.to/a", full: "https://attachments.f95zone.to/a" };
    assert.deepEqual(screenshotUrls(pair.thumb), pair);
    assert.deepEqual(screenshotUrls(pair.full), pair);
    assert.deepEqual(screenshotUrls("https://i.imgur.com/a"), { thumb: "https://i.imgur.com/a", full: "https://i.imgur.com/a" });
});
