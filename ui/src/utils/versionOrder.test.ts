// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compareVersions, sameVersion } from "./versionOrder.ts";

const fixture = new URL("../../../tests/fixtures/version_order_cases.json", import.meta.url);
const cases = JSON.parse(readFileSync(fixture, "utf8")) as {
  same_version: [string, string, boolean][];
  compare_versions: [string, string, -1 | 0 | 1 | null][];
};

for (const [a, b, expected] of cases.same_version) {
  test(`sameVersion compares "${a}" with "${b}"`, () => {
    assert.equal(sameVersion(a, b), expected);
  });
}

for (const [a, b, expected] of cases.compare_versions) {
  test(`compareVersions orders "${a}" and "${b}"`, () => {
    assert.equal(compareVersions(a, b), expected);
  });
}
