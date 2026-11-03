// SPDX-License-Identifier: GPL-3.0-or-later
import { strict as assert } from "node:assert";
import { test } from "node:test";
import { switchWarning, unityActions, unityWarnings } from "./unityTools.ts";
import type { UnityToolsState, BepInExBuild } from "../services/api.ts";

const state: UnityToolsState = { platform: "windows", build: { platform: "windows", data_folder: "", backend: "il2cpp", arch: "x64", version: "" }, installed: null, has_backup: false, unity_libs: false };
const build: BepInExBuild = { id: "6", major: 6, backend: "il2cpp", channel: "be", date: "" };
test("Unity actions and warnings", () => {
  assert.equal(unityActions(state, build, false).install.enabled, true);
  assert.equal(unityActions(state, build, true).install.enabled, false);
  assert.equal(unityWarnings(state).length, 2);
  assert.match(unityActions({ ...state, platform: "linux" }, build, false).install.message, /Linux-native/);
  assert.match(switchWarning({ ...state, installed: { version: "5", major: 5, backend: "mono", complete: true, managed_by_wlib: false } }, build), /plugins/);
});
