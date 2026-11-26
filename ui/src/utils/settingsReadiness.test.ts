// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { isProtonRunner, isRunnerPath, optionalToolsSummary, runnerLabel, windowsReadiness, type UmuReadiness } from "./settingsReadiness.ts";

const wine = [{ name: "System Wine", path: "wine" }, { name: "GE-Proton (latest)", path: "GE-Proton" }, { name: "UMU-Proton", path: "UMU-Proton" }];
const noWine = wine.slice(1);
const umuReady: UmuReadiness = { installed: true, python3: true, runtimeReady: true, busy: false };

test("Proton runners follow the launcher's rule", () => {
  assert.equal(isProtonRunner("GE-Proton"), true);
  assert.equal(isProtonRunner("/home/u/.steam/root/compatibilitytools.d/GE-Proton9-20/proton"), true);
  assert.equal(isProtonRunner("/opt/GE-Proton9-20/"), true);
  assert.equal(isProtonRunner("wine"), false);
  assert.equal(isProtonRunner("/home/u/lutris/runners/wine/wine-ge/bin/wine"), false);
  assert.equal(isRunnerPath(""), false);
  assert.equal(isRunnerPath("UMU-Proton"), false);
  assert.equal(isRunnerPath("/usr/bin/wine"), true);
});

test("runner labels use the list name, else the build folder", () => {
  assert.equal(runnerLabel("GE-Proton", wine), "GE-Proton (latest)");
  assert.equal(runnerLabel("/x/compatibilitytools.d/GE-Proton9-20/proton", wine), "GE-Proton9-20");
  assert.equal(runnerLabel("/x/wine/wine-ge/bin/wine", wine), "wine-ge");
  assert.equal(runnerLabel("/opt/mywine", wine), "mywine");
});

test("the default runner is system Wine when Wine is installed: ready, umu not needed", () => {
  const result = windowsReadiness({ runner: "", runners: wine, runnerPathOk: null, umu: null });
  assert.equal(result.tone, "ready");
  assert.equal(result.usesProton, false);
  assert.match(result.text, /ready.*System Wine/);
});

test("without Wine the default is UMU-Proton, which needs umu-launcher", () => {
  const result = windowsReadiness({ runner: "", runners: noWine, runnerPathOk: null, umu: { ...umuReady, installed: false, runtimeReady: false } });
  assert.equal(result.tone, "action");
  assert.equal(result.usesProton, true);
  assert.match(result.text, /UMU-Proton needs umu-launcher\. Wine is not installed/);
  assert.deepEqual(result.action, { label: "Set up Proton", target: "umu" });
});

test("a Proton runner needs the runtime after umu, then is ready", () => {
  const base = { runner: "GE-Proton", runners: wine, runnerPathOk: null };
  assert.match(windowsReadiness({ ...base, umu: { ...umuReady, runtimeReady: false } }).text, /needs the Steam Linux Runtime/);
  assert.match(windowsReadiness({ ...base, umu: { ...umuReady, python3: false } }).text, /needs Python 3/);
  assert.equal(windowsReadiness({ ...base, umu: { ...umuReady, busy: true } }).tone, "checking");
  const ready = windowsReadiness({ ...base, umu: umuReady });
  assert.equal(ready.tone, "ready");
  assert.match(ready.text, /GE-Proton \(latest\) through umu-launcher/);
});

test("unknown states say checking, never ready", () => {
  assert.equal(windowsReadiness({ runner: "", runners: null, runnerPathOk: null, umu: umuReady }).tone, "checking");
  assert.equal(windowsReadiness({ runner: "GE-Proton", runners: wine, runnerPathOk: null, umu: null }).tone, "checking");
  assert.equal(windowsReadiness({ runner: "/opt/wine/bin/wine", runners: wine, runnerPathOk: null, umu: null }).tone, "checking");
});

test("a typed runner path that does not exist asks to check the path", () => {
  const result = windowsReadiness({ runner: "/missing/wine", runners: wine, runnerPathOk: false, umu: null });
  assert.equal(result.tone, "action");
  assert.deepEqual(result.action, { label: "Check the path", target: "wine-proton" });
  assert.equal(windowsReadiness({ runner: "/opt/wine/bin/wine", runners: wine, runnerPathOk: true, umu: null }).tone, "ready");
});

test("optional tools count what is installed and name what is not", () => {
  assert.deepEqual(optionalToolsSummary([{ name: "GameMode", installed: true }, { name: "MangoHud", installed: false }, { name: "Gamescope", installed: false }]),
    { tone: "ready", text: "Optional tools: 1 of 3 installed.", missing: ["MangoHud", "Gamescope"] });
  assert.equal(optionalToolsSummary([{ name: "GameMode", installed: null }]).tone, "checking");
});
