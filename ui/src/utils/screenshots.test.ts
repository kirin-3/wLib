// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { displayScreenshots, screenshotUrls } from "./screenshots.ts";

test("maps either F95 media host and preserves other URLs", () => {
    const pair = { thumb: "https://preview.f95zone.to/a", full: "https://attachments.f95zone.to/a" };
    assert.deepEqual(screenshotUrls(pair.thumb), pair);
    assert.deepEqual(screenshotUrls(pair.full), pair);
    assert.deepEqual(screenshotUrls("https://i.imgur.com/a"), { thumb: "https://i.imgur.com/a", full: "https://i.imgur.com/a" });
});

test("keeps local LC addresses and never shows a LewdCorner URL", () => {
    const local = "http://127.0.0.1:8183/lc-images/0123456789abcdef0123456789abcdef";
    const lc = "https://lewdcorner.com/attachments/screenshot1-webp.891913/";
    assert.deepEqual(screenshotUrls(local), { thumb: local, full: local });
    const imgur = screenshotUrls("https://i.imgur.com/a");
    assert.deepEqual(displayScreenshots([{ thumb: local, full: local }, screenshotUrls(lc), imgur]), [{ thumb: local, full: local }, imgur]);
    assert.deepEqual(displayScreenshots([{ thumb: "https://i.imgur.com/t", full: "https://cdn.lewdcorner.com/x.png" }]), []);
});
