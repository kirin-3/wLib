// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { inputModality, setInputModality } from "./inputModality.ts";
test("input modality updates both state and the document", () => {
  const dataset: Record<string, string> = {};
  Object.defineProperty(globalThis, "document", { configurable: true, value: { documentElement: { dataset } } });
  setInputModality("controller"); assert.equal(dataset.input, "controller"); assert.equal(inputModality.value, "controller");
  setInputModality("pointer"); assert.equal(dataset.input, "pointer"); assert.equal(inputModality.value, "pointer");
  Reflect.deleteProperty(globalThis, "document");
});
