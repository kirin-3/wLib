// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import { launchNoticeMessage, normalizeHtmlPlayer, resolveWebPlayer } from "./webPlayer.ts";

test("html follows the per-game choice, then the setting", () => {
  assert.equal(resolveWebPlayer("/g/index.html", "auto", "", true, "linux"), "builtin");
  assert.equal(resolveWebPlayer("/g/index.HTM", "native", "", undefined, "linux"), "builtin");
  assert.equal(resolveWebPlayer("/g/index.html", "auto", "", false, "linux"), "browser");
  assert.equal(resolveWebPlayer("/g/index.html", "auto", "builtin", false, "linux"), "builtin");
  assert.equal(resolveWebPlayer("/g/index.html", "auto", "browser", true, "linux"), "browser");
  assert.equal(resolveWebPlayer("/g/index.html", "auto", "bogus", false, "linux"), "browser");
});

test("swf always uses the built-in player", () => {
  assert.equal(resolveWebPlayer("/g/game.swf", "auto", "browser", false, "linux"), "builtin");
  assert.equal(resolveWebPlayer("C:\\g\\GAME.SWF", "auto", "", true, "windows"), "builtin");
});

test("other targets and real-process launch modes are not web launches", () => {
  assert.equal(resolveWebPlayer("/g/game.exe", "auto", "", true, "linux"), null);
  assert.equal(resolveWebPlayer("", "auto", "", true, "linux"), null);
  for (const mode of ["wine_proton", "custom", "rpgmaker_linux"]) {
    assert.equal(resolveWebPlayer("/g/index.html", mode, "builtin", true, "linux"), null, mode);
    assert.equal(resolveWebPlayer("/g/game.swf", mode, "", true, "linux"), null, mode);
  }
  assert.equal(resolveWebPlayer("C:\\g\\index.html", "wine_proton", "", false, "windows"), "browser");
});

test("normalizeHtmlPlayer drops unknown values and the notice code expands to guidance", () => {
  assert.equal(normalizeHtmlPlayer("Browser"), "");
  assert.equal(normalizeHtmlPlayer("browser"), "browser");
  assert.match(launchNoticeMessage("html_player_first_use"), /System browser/);
  assert.equal(launchNoticeMessage("Some other notice"), "Some other notice");
});
