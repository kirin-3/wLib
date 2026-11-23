// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RpgmPluginEntry, RpgmPluginsState } from "../services/api.ts";
import {
  filterPlugins,
  issueLabel,
  issueText,
  pluginCounts,
  pluginRestoreState,
  pluginSwitchState,
  restoreConfirmation,
  switchConfirmation,
} from "./rpgmPlugins.ts";

const entry = (index: number, name: string, extra: Partial<RpgmPluginEntry> = {}): RpgmPluginEntry => ({
  index,
  name,
  description: "",
  enabled: true,
  loaded: true,
  can_enable: true,
  needed_by: [],
  issues: [],
  ...extra,
});

const state = (backup: string, extra: Partial<RpgmPluginsState> = {}): RpgmPluginsState =>
  ({ success: true, engine: "mz", readable: true, revision: "r", entries: [], backup: { state: backup, reason: "" }, ...extra }) as RpgmPluginsState;

test("search matches name and description and keeps the entry index", () => {
  const entries = [entry(0, "Core"), entry(11, "MessagePlus"), entry(12, "Log", { description: "Message LOG" })];
  assert.deepEqual(filterPlugins(entries, "message").map((e) => e.index), [11, 12]);
  assert.deepEqual(filterPlugins(entries, "  ").map((e) => e.index), [0, 11, 12]);
  assert.deepEqual(filterPlugins(entries, "zzz"), []);
});

test("counts on and off", () => {
  assert.deepEqual(pluginCounts([entry(0, "A"), entry(1, "B", { enabled: false }), entry(2, "C")]), { on: 2, off: 1 });
});

test("issue texts and labels", () => {
  assert.match(issueText({ code: "missing_file", level: "warning", names: [] }), /^File is missing/);
  assert.match(issueText({ code: "missing_file", level: "note", names: [] }), /cannot be switched on/);
  assert.equal(issueText({ code: "missing_dependency", level: "warning", names: ["CoreEngine"] }), "Needs CoreEngine, which does not load.");
  assert.equal(issueText({ code: "wrong_order", level: "warning", direction: "after", names: ["Core"] }), "Must load after Core.");
  assert.equal(issueText({ code: "wrong_order", level: "warning", direction: "before", names: ["Core"] }), "Must load before Core.");
  assert.match(issueText({ code: "duplicate", level: "note", names: [] }), /^Not loaded, duplicate/);
  assert.match(issueText({ code: "case_mismatch", level: "note", names: [] }), /case-sensitive/);
  assert.match(issueText({ code: "unsafe_name", level: "warning", names: [] }), /Unsafe name/);
  assert.equal(issueLabel({ code: "duplicate", level: "note", names: [] }), "Note");
  assert.equal(issueLabel({ code: "unsafe_name", level: "warning", names: [] }), "Warning");
});

test("switch state blocks only switching on a missing or unsafe plugin", () => {
  assert.deepEqual(pluginSwitchState(entry(0, "A"), false), { enabled: true, reason: "" });
  assert.deepEqual(pluginSwitchState(entry(0, "A"), true), { enabled: false, reason: "" });
  const missing = entry(0, "A", { enabled: false, can_enable: false, issues: [{ code: "missing_file", level: "note", names: [] }] });
  assert.deepEqual(pluginSwitchState(missing, false), { enabled: false, reason: "Its file is missing" });
  const unsafe = entry(0, "A", { enabled: false, can_enable: false, issues: [{ code: "unsafe_name", level: "warning", names: [] }] });
  assert.equal(pluginSwitchState(unsafe, false).reason, "Its name is unsafe");
  const loadedMissing = entry(0, "A", { can_enable: false });
  assert.equal(pluginSwitchState(loadedMissing, false).enabled, true);
});

test("restore state per backup state", () => {
  assert.equal(pluginRestoreState(state("none"), false).visible, false);
  assert.equal(pluginRestoreState(state("same"), false).visible, false);
  assert.deepEqual(pluginRestoreState(state("toggles"), false), { visible: true, enabled: true, confirm: false, reason: "" });
  assert.deepEqual(pluginRestoreState(state("changed"), true), { visible: true, enabled: false, confirm: true, reason: "" });
  const invalid = pluginRestoreState(state("invalid", { backup: { state: "invalid", reason: "The backup is a symbolic link" } }), false);
  assert.equal(invalid.visible, false);
  assert.match(invalid.reason, /symbolic link/);
});

test("switch confirmation: save warning, needed by, both and none", () => {
  const core = entry(0, "CoreEngine", { needed_by: ["A", "B"] });
  const plain = entry(1, "Plain");

  const save = switchConfirmation(state("none"), plain, false);
  assert.ok(save);
  assert.match(save.message, /break existing saves/);
  assert.doesNotMatch(save.message, /declare/);
  assert.equal(save.danger, false);

  const needed = switchConfirmation(state("toggles"), core, false);
  assert.ok(needed);
  assert.match(needed.message, /2 loaded plugins declare that they need CoreEngine: A, B/);
  assert.doesNotMatch(needed.message, /saves/);
  assert.equal(needed.danger, true);

  const both = switchConfirmation(state("none"), core, false);
  assert.ok(both);
  assert.match(both.message, /need CoreEngine/);
  assert.match(both.message, /saves/);

  assert.equal(switchConfirmation(state("toggles"), plain, false), null);
  assert.equal(switchConfirmation(state("toggles"), core, true), null);
  assert.equal(switchConfirmation(state("toggles", { engine: "mv" }), core, false), null);
  assert.equal(switchConfirmation(state("same"), entry(2, "Dup", { loaded: false, needed_by: ["A"] }), false), null);
});

test("restore confirmation only for a changed list", () => {
  assert.equal(restoreConfirmation(state("toggles")), null);
  const confirm = restoreConfirmation(state("changed"));
  assert.ok(confirm);
  assert.equal(confirm.danger, true);
  assert.match(confirm.message, /will be lost/);
});
