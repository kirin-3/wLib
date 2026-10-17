// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { getSafeNotesLinkUrl } from "./notesLinks.ts";

test("notes links allow only absolute http and https hrefs", () => {
  assert.equal(
    getSafeNotesLinkUrl("https://example.com/guide"),
    "https://example.com/guide",
  );
  assert.equal(
    getSafeNotesLinkUrl("http://example.com/page?q=1"),
    "http://example.com/page?q=1",
  );
  assert.equal(getSafeNotesLinkUrl("file:///tmp/guide.pdf"), null);
  assert.equal(getSafeNotesLinkUrl("javascript:alert(1)"), null);
  assert.equal(getSafeNotesLinkUrl("/relative/path"), null);
  assert.equal(getSafeNotesLinkUrl("page.html"), null);
  assert.equal(getSafeNotesLinkUrl("https://"), null);
  assert.equal(getSafeNotesLinkUrl(""), null);
  assert.equal(getSafeNotesLinkUrl(null), null);
  assert.equal(getSafeNotesLinkUrl(undefined), null);
});
