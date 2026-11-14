// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { titleInitials, titleSwatch } from "./coverPlaceholder.ts";

test("two-word title uses the first letter of each word", () => {
  assert.equal(titleInitials("Cold Station"), "CS");
  assert.equal(titleInitials("being a DIK"), "BA");
  assert.equal(titleInitials("  The   Last Hope  "), "TL");
});

test("one-word title uses its first two letters", () => {
  assert.equal(titleInitials("Eternum"), "ET");
  assert.equal(titleInitials("X"), "X");
});

test("digits count as letters and punctuation is ignored", () => {
  assert.equal(titleInitials("2 Days"), "2D");
  assert.equal(titleInitials("[Ren'Py] Summer"), "RS");
  assert.equal(titleInitials("Élan vital"), "ÉV");
});

test("punctuation-only and empty titles have no initials", () => {
  assert.equal(titleInitials("???"), "");
  assert.equal(titleInitials("- !! -"), "");
  assert.equal(titleInitials(""), "");
  assert.equal(titleInitials(null), "");
});

test("swatch is stable for the same title and ignores case and outer spaces", () => {
  const swatch = titleSwatch("Cold Station");
  assert.equal(titleSwatch("Cold Station"), swatch);
  assert.equal(titleSwatch("  cold station "), swatch);
});

test("swatch is always a defined tag swatch", () => {
  const source = readFileSync(new URL("./tagColors.ts", import.meta.url), "utf8");
  const list = source.match(/TAG_COLOR_SWATCHES = (\[[^\]]*\])/)?.[1];
  assert.ok(list);
  const swatches = JSON.parse(list) as string[];
  const seen = new Set<string>();
  for (const title of ["Cold Station", "Eternum", "???", "", "A", "Summertime Saga", "Being a DIK", "Lust Academy", "Treasure of Nadia"]) {
    const swatch = titleSwatch(title);
    assert.ok(swatches.includes(swatch), swatch);
    seen.add(swatch);
  }
  assert.ok(seen.size > 1, "different titles spread over several swatches");
});
