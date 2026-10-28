// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  consoleHint,
  formatToolBytes,
  formatToolProgress,
  getDecryptState,
  getExtractState,
  getResumableToolAction,
  getUrmToggleState,
  getUseDecryptedState,
  keySourceLabel,
  shouldSyncDeveloperOption,
  toolProgressPercent,
} from "./gameTools.ts";

const rpgm = (overrides = {}) => ({
  encrypted: 10,
  images: 6,
  audio: 4,
  missing: 10,
  key_source: "system" as const,
  use_decrypted: false,
  backup: false,
  ...overrides,
});

test("progress label and percent", () => {
  assert.equal(formatToolProgress({ running: true, done: 0, total: 0, current: "" }), "Working...");
  assert.equal(formatToolProgress({ running: true, done: 12, total: 100, current: "bg/room.png" }), "12 of 100 files - bg/room.png");
  assert.equal(formatToolProgress({ running: true, done: 1, total: 2, current: "" }), "1 of 2 files");
  assert.equal(toolProgressPercent({ running: true, done: 1, total: 3, current: "" }), 33);
  assert.equal(toolProgressPercent({ running: true, done: 9, total: 3, current: "" }), 100);
  assert.equal(toolProgressPercent({ running: true, done: 1, total: 0, current: "" }), 0);
});

test("byte formatting", () => {
  assert.equal(formatToolBytes(512), "512 B");
  assert.equal(formatToolBytes(52428800), "50.0 MB");
  assert.equal(formatToolBytes(-1), "");
});

test("extract availability explains why it is off", () => {
  const archive = { name: "a.rpa", size: 1, format: "RPA-3.0" };
  assert.equal(getExtractState([archive], "folder", "/out", false).enabled, true);
  assert.equal(getExtractState([archive], "in_place", "", false).enabled, true);
  assert.match(getExtractState([archive], "folder", " ", false).message, /output folder/);
  assert.match(getExtractState([], "in_place", "", false).message, /no archives/);
  assert.match(getExtractState([{ ...archive, format: "" }], "in_place", "", false).message, /format/);
  assert.match(getExtractState([archive], "folder", "/out", true).message, /running/);
});

test("decrypt availability", () => {
  assert.equal(getDecryptState(rpgm(), false).enabled, true);
  assert.match(getDecryptState(rpgm({ encrypted: 0, missing: 0 }), false).message, /not encrypted/);
  assert.match(getDecryptState(rpgm({ missing: 0 }), false).message, /already/);
  assert.match(getDecryptState(rpgm({ key_source: "" }), false).message, /key/);
  assert.equal(getDecryptState(rpgm(), true).enabled, false);
});

test("use-decrypted switch availability", () => {
  assert.match(getUseDecryptedState(rpgm(), false).message, /Decrypt 10 more assets first/);
  assert.match(getUseDecryptedState(rpgm({ missing: 1 }), false).message, /1 more asset first/);
  assert.equal(getUseDecryptedState(rpgm({ missing: 0 }), false).enabled, true);
  assert.equal(getUseDecryptedState(rpgm({ missing: 5, use_decrypted: true }), false).enabled, true);
  assert.equal(getUseDecryptedState(rpgm({ missing: 0 }), true).enabled, false);
  assert.match(getUseDecryptedState(rpgm({ encrypted: 0, missing: 0 }), false).message, /not encrypted/);
});

test("URM can be removed without the source file", () => {
  assert.equal(getUrmToggleState({ installed: true, source_configured: false }, false).enabled, true);
  assert.equal(getUrmToggleState({ installed: true, source_configured: true }, false).enabled, true);
  assert.equal(getUrmToggleState({ installed: false, source_configured: true }, false).enabled, true);
  const missing = getUrmToggleState({ installed: false, source_configured: false }, false);
  assert.equal(missing.enabled, false);
  assert.match(missing.message, /URM \.rpa source/);
  assert.equal(getUrmToggleState({ installed: true, source_configured: false }, true).enabled, false);
});

test("only long runs are re-attached after reopening the tab", () => {
  const status = (running: boolean, action: string) => ({ running, action, done: 0, total: 0, current: "", cancel: false });
  assert.equal(getResumableToolAction(status(true, "extract")), "extract");
  assert.equal(getResumableToolAction(status(true, "decrypt")), "decrypt");
  assert.equal(getResumableToolAction(status(true, "console")), "");
  assert.equal(getResumableToolAction(status(true, "urm")), "");
  assert.equal(getResumableToolAction(status(false, "extract")), "");
});

test("a pending developer-mode choice survives a reload that leaves the console off", () => {
  assert.equal(shouldSyncDeveloperOption("off", "off"), false);
  assert.equal(shouldSyncDeveloperOption(undefined, "off"), true);
  assert.equal(shouldSyncDeveloperOption("on", "off"), true);
  assert.equal(shouldSyncDeveloperOption("off", "on"), true);
  assert.equal(shouldSyncDeveloperOption("off", "developer"), true);
  assert.equal(shouldSyncDeveloperOption("developer", "developer"), true);
});

test("console hint and key label", () => {
  assert.equal(consoleHint("off"), "");
  assert.match(consoleHint("on"), /Shift\+O/);
  assert.match(consoleHint("developer"), /Shift\+D/);
  assert.equal(keySourceLabel("system"), "Key read from System.json");
  assert.match(keySourceLabel("recovered"), /recovered/);
  assert.equal(keySourceLabel(""), "No key found");
});
