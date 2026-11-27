// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { NO_SCROLL_EDGES, sameScrollEdges, scrollEdges } from "./scrollEdges.ts";

test("content that fits has no edges", () => {
  assert.deepEqual(scrollEdges({ scrollTop: 0, scrollHeight: 400, clientHeight: 400 }), NO_SCROLL_EDGES);
  // Sub-pixel overflow from rounding does not count as scrollable.
  assert.deepEqual(scrollEdges({ scrollTop: 0, scrollHeight: 401.5, clientHeight: 400 }), NO_SCROLL_EDGES);
});

test("tall content shows the bottom edge at the top and the top edge at the end", () => {
  assert.deepEqual(scrollEdges({ scrollTop: 0, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: false, bottom: true });
  assert.deepEqual(scrollEdges({ scrollTop: 200, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: true, bottom: true });
  assert.deepEqual(scrollEdges({ scrollTop: 500, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: true, bottom: false });
  // One stray pixel short of the end still counts as the end.
  assert.deepEqual(scrollEdges({ scrollTop: 499, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: true, bottom: false });
});

test("overscroll past either end is clamped", () => {
  assert.deepEqual(scrollEdges({ scrollTop: -30, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: false, bottom: true });
  assert.deepEqual(scrollEdges({ scrollTop: 560, scrollHeight: 900, clientHeight: 400 }), { scrollable: true, top: true, bottom: false });
});

test("sameScrollEdges compares every field", () => {
  const a = scrollEdges({ scrollTop: 0, scrollHeight: 900, clientHeight: 400 });
  assert.ok(sameScrollEdges(a, { scrollable: true, top: false, bottom: true }));
  assert.ok(!sameScrollEdges(a, { scrollable: true, top: true, bottom: true }));
  assert.ok(!sameScrollEdges(NO_SCROLL_EDGES, a));
});
