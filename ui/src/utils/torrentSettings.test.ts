// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseTorrentField, SEED_MODE_OPTIONS, torrentFieldKey, torrentFieldText } from "./torrentSettings.ts";

test("seed mode offers the three choices, asking first", () => {
  assert.deepEqual(SEED_MODE_OPTIONS.map((o) => o.label), ["Ask each time", "Always seed", "Never seed"]);
  assert.equal(SEED_MODE_OPTIONS[0]?.value, "ask");
});

test("numbers are validated and blanks mean no limit", () => {
  assert.deepEqual(parseTorrentField("ratio", "1.5"), { value: 1.5, error: "" });
  assert.deepEqual(parseTorrentField("ratio", ""), { value: 0, error: "" });
  assert.deepEqual(parseTorrentField("hours", ""), { value: "", error: "" });
  assert.deepEqual(parseTorrentField("hours", " 12 "), { value: 12, error: "" });
  assert.deepEqual(parseTorrentField("upload", "500"), { value: 500, error: "" });
  assert.equal(parseTorrentField("upload", "1.5").value, null);
  assert.equal(parseTorrentField("upload", "fast").value, null);
  assert.equal(parseTorrentField("ratio", "-1").value, null);
});

test("fields map to settings keys and show zero as blank", () => {
  assert.equal(torrentFieldKey("upload"), "torrent_upload_limit_kib");
  assert.equal(torrentFieldText(0), "");
  assert.equal(torrentFieldText(""), "");
  assert.equal(torrentFieldText(1), "1");
  assert.equal(torrentFieldText(undefined), "");
});

test("the torrent settings no longer hold a download limit", () => {
  assert.deepEqual(["ratio", "hours", "upload"].map((field) => torrentFieldKey(field as "ratio" | "hours" | "upload")),
    ["torrent_seed_ratio", "torrent_seed_hours", "torrent_upload_limit_kib"]);
});
