// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import {
  gridColumnCount,
  INITIAL_WINDOW,
  measuredPitch,
  MIN_BUFFER_ROWS,
  renderWindow,
  sameWindow,
  steadyMetrics,
} from "./libraryWindow.ts";

const grid = { columns: 6, pitch: 300 };
const screen = { top: 0, height: 1000 };

test("before the first measurement the first cards are rendered", () => {
  assert.deepEqual(renderWindow(1000, null, screen), { start: 0, end: INITIAL_WINDOW, padTop: 0, padBottom: 0, primary: 0 });
  assert.deepEqual(renderWindow(10, { columns: 6, pitch: 0 }, screen), { start: 0, end: 10, padTop: 0, padBottom: 0, primary: 0 });
  assert.deepEqual(renderWindow(0, grid, screen), { start: 0, end: 0, padTop: 0, padBottom: 0, primary: 0 });
});

test("at the top the window covers the screen and one screen below", () => {
  const win = renderWindow(1000, grid, screen);
  // Visible rows 0-3 (ceil(1000 / 300) = 4), buffer 4 rows below.
  assert.equal(win.start, 0);
  assert.equal(win.end, 8 * 6);
  assert.equal(win.padTop, 0);
  assert.equal(win.padBottom, (167 - 8) * 300);
  assert.equal(win.primary, 0);
});

test("in the middle the window starts at a row and keeps the total height", () => {
  const total = 1000;
  const rows = Math.ceil(total / grid.columns);
  const win = renderWindow(total, grid, { top: 30000, height: 1000 });
  assert.equal(win.start % grid.columns, 0);
  assert.equal(win.start, (100 - 4) * 6);
  assert.equal(win.end, (104 + 4) * 6);
  assert.equal(win.primary, 100 * 6);
  const renderedRows = Math.ceil((win.end - win.start) / grid.columns);
  assert.equal(win.padTop + renderedRows * grid.pitch + win.padBottom, rows * grid.pitch);
});

test("at the end the last game is rendered and no space follows", () => {
  const win = renderWindow(1000, grid, { top: 167 * 300 - 1000, height: 1000 });
  assert.equal(win.end, 1000);
  assert.equal(win.padBottom, 0);
  // Scrolled past the end (elastic scroll or a shrunk list) still ends at the last game.
  const past = renderWindow(1000, grid, { top: 999999, height: 1000 });
  assert.equal(past.end, 1000);
  assert.ok(past.start < past.end);
  assert.ok(past.primary < 1000);
});

test("the window size does not depend on the game count", () => {
  const view = { top: 15000, height: 1000 };
  const small = renderWindow(1000, grid, view);
  const large = renderWindow(2000, grid, view);
  assert.equal(small.end - small.start, large.end - large.start);
});

test("a small library is rendered whole", () => {
  assert.deepEqual(renderWindow(10, grid, screen), { start: 0, end: 10, padTop: 0, padBottom: 0, primary: 0 });
});

test("one column (the list) uses at least the minimum buffer", () => {
  const win = renderWindow(1000, { columns: 1, pitch: 48 }, { top: 4800, height: 40 });
  assert.equal(win.start, 100 - MIN_BUFFER_ROWS);
  assert.equal(win.end, 101 + MIN_BUFFER_ROWS);
  assert.equal(win.primary, 100);
});

test("fewer columns after a resize still start the window at a row", () => {
  const win = renderWindow(1000, { columns: 4, pitch: 300 }, { top: 9000, height: 1000 });
  assert.equal(win.start % 4, 0);
  assert.equal(win.primary % 4, 0);
});

test("the primary card is the first row that shows at least half", () => {
  assert.equal(renderWindow(1000, grid, { top: 3100, height: 1000 }).primary, 10 * 6);
  assert.equal(renderWindow(1000, grid, { top: 3200, height: 1000 }).primary, 11 * 6);
});

test("the focused card keeps the rows around it when it is ahead of the screen", () => {
  // Visible rows 100-103, buffer 4: rows 96-107 without focus.
  const view = { top: 30000, height: 1000 };
  const ahead = renderWindow(1000, grid, view, 115 * 6 + 2);
  assert.equal(ahead.start, 96 * 6);
  assert.equal(ahead.end, (115 + 1 + MIN_BUFFER_ROWS) * 6);
  const behind = renderWindow(1000, grid, view, 90 * 6);
  assert.equal(behind.start, (90 - MIN_BUFFER_ROWS) * 6);
  assert.equal(behind.end, 108 * 6);
  // Far from the screen (after a mouse scroll) the focused card is let go.
  assert.deepEqual(renderWindow(1000, grid, view, 0), renderWindow(1000, grid, view));
  assert.deepEqual(renderWindow(1000, grid, view, 5000), renderWindow(1000, grid, view));
  // While focus is moving it is kept at any distance.
  const moving = renderWindow(1000, grid, view, 130 * 6, true);
  assert.equal(moving.start, 96 * 6);
  assert.equal(moving.end, (130 + 1 + MIN_BUFFER_ROWS) * 6);
});

test("a pitch within 1 px of the old one is kept", () => {
  const old = { columns: 6, pitch: 300 };
  assert.equal(steadyMetrics(old, { columns: 6, pitch: 300.6 }), old);
  assert.deepEqual(steadyMetrics(old, { columns: 6, pitch: 302 }), { columns: 6, pitch: 302 });
  assert.deepEqual(steadyMetrics(old, { columns: 5, pitch: 300 }), { columns: 5, pitch: 300 });
  assert.deepEqual(steadyMetrics(null, old), old);
});

test("windows compare by value", () => {
  assert.ok(sameWindow(renderWindow(1000, grid, screen), renderWindow(1000, grid, { top: 10, height: 1000 })));
  assert.ok(!sameWindow(renderWindow(1000, grid, screen), renderWindow(1000, grid, { top: 3000, height: 1000 })));
});

test("the column count comes from the computed grid tracks", () => {
  assert.equal(gridColumnCount("220px 220px 220px"), 3);
  assert.equal(gridColumnCount(" 233.5px  233.5px "), 2);
  assert.equal(gridColumnCount("none"), 1);
  assert.equal(gridColumnCount(""), 1);
});

test("the pitch is measured from the first card of the first and last row", () => {
  assert.equal(measuredPitch(0, 632, 3, 300, 16), 316);
  assert.equal(measuredPitch(10, 10, 1, 300, 16), 316);
  assert.equal(measuredPitch(0, 0, 0, 300, 16), 0);
  assert.equal(measuredPitch(100, 196, 3, 48, 0), 48);
});
