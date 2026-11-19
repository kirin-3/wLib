// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { EXTENSION_SITE_SWITCHES } from "./extensionSiteSettings.ts";

test("the guide lists the extension's switches in options page order", () => {
  const options = readFileSync(new URL("../../../extension/options.html", import.meta.url), "utf8");
  const switchLabels = [...options.matchAll(/<label\b[^>]*>\s*<input\b[^>]*\btype="checkbox"[^>]*>([^<]+)<\/label>/g)]
    .map(([, name]) => name!.trim());
  assert.deepEqual(EXTENSION_SITE_SWITCHES.map(({ name }) => name), switchLabels);
});
