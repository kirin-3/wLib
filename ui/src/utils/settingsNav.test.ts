// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { atScrollEnd, pickActiveSection } from "./settingsNav.ts";

const sections = [{ id: "library", top: -900 }, { id: "downloads", top: -100 }, { id: "steam", top: 300 }, { id: "advanced", top: 600 }];
const middle = { scrollTop: 1000, scrollHeight: 5000, clientHeight: 800 };

test("the last section above the probe line is current", () => {
  assert.equal(pickActiveSection(sections, 20, middle), "downloads");
  assert.equal(pickActiveSection(sections, 300, middle), "steam");
});

test("the first section is current when none reached the probe line", () => {
  assert.equal(pickActiveSection([{ id: "library", top: 40 }, { id: "downloads", top: 900 }], 20, { scrollTop: 0, scrollHeight: 5000, clientHeight: 800 }), "library");
});

test("at the end of the page the last section is current", () => {
  assert.equal(pickActiveSection(sections, 20, { scrollTop: 4200, scrollHeight: 5000, clientHeight: 800 }), "advanced");
  assert.equal(pickActiveSection(sections, 20, { scrollTop: 4198.5, scrollHeight: 5000, clientHeight: 800 }), "advanced");
  assert.equal(pickActiveSection(sections, 20, { scrollTop: 4190, scrollHeight: 5000, clientHeight: 800 }), "downloads");
});

test("a page that cannot scroll is not at its end", () => {
  const box = { scrollTop: 0, scrollHeight: 800, clientHeight: 800 };
  assert.equal(atScrollEnd(box), false);
  assert.equal(pickActiveSection([{ id: "appearance", top: 40 }, { id: "advanced", top: 300 }], 60, box), "appearance");
});

test("no sections gives no current section", () => {
  assert.equal(pickActiveSection([], 20, middle), "");
});
