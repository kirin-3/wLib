// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { pickNext, primaryCandidate, focusCandidates } from "./spatialFocus.ts";
const rect = (left: number, top: number) => ({ left, top, right: left + 10, bottom: top + 10 });
test("moves in a grid and stays at the edge", () => {
  const center = rect(20, 20), up = rect(20, 0), down = rect(20, 40), left = rect(0, 20), right = rect(40, 20);
  const all = [up, down, left, right];
  assert.equal(pickNext(center, all, "up"), up);
  assert.equal(pickNext(center, all, "down"), down);
  assert.equal(pickNext(center, all, "left"), left);
  assert.equal(pickNext(center, all, "right"), right);
  assert.equal(pickNext(right, [center], "right"), null);
});
test("prefers aligned controls and the marked initial focus", () => {
  const row = rect(0, 0), aligned = rect(30, 0), diagonal = rect(20, 20);
  assert.equal(pickNext(row, [diagonal, aligned], "right"), aligned);
  const options = [{ primary: false }, { primary: true }];
  assert.equal(primaryCandidate(options), options[1]);
  assert.equal(primaryCandidate([options[0]!]), options[0]);
  assert.equal(primaryCandidate([]), null);
});
test("a wide adjacent input wins over a diagonally closer button", () => {
  const browse = { left: 787, right: 871, top: 357, bottom: 397 };
  const input = { left: 329, right: 779, top: 357, bottom: 397 };
  const cancel = { left: 710, right: 806, top: 437, bottom: 477 };
  assert.equal(pickNext(browse, [cancel, input], "left"), input);
});


test("visible toast controls are directional focus candidates", () => {
  const button = { getClientRects: () => [1], matches: () => false, closest: () => null };
  const root = { querySelectorAll: () => [button] };
  Object.assign(globalThis, { getComputedStyle: () => ({ visibility: "visible" }) });
  assert.deepEqual(focusCandidates(root as unknown as HTMLElement), [button]);
});
