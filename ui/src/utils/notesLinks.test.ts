// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { getSafeNotesLinkUrl, renderedLinkHref } from "./notesLinks.ts";

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

test("rendered links resolve from the clicked element and only to safe URLs", () => {
  const anchor = { getAttribute: (name: string) => (name === "href" ? "https://example.com/notes" : null) };
  const target = { closest: (selector: string) => (selector === "a[href]" ? anchor : null) };
  const href = renderedLinkHref(target);
  assert.equal(href, "https://example.com/notes");
  assert.equal(getSafeNotesLinkUrl(href), "https://example.com/notes");

  // Clicks outside a link, and links with foreign or missing hrefs, are left alone.
  assert.equal(renderedLinkHref({ closest: () => null }), null);
  assert.equal(renderedLinkHref(null), null);
  assert.equal(renderedLinkHref("text"), null);
  const scriptAnchor = { getAttribute: () => "javascript:alert(1)" };
  const scriptTarget = { closest: () => scriptAnchor };
  assert.equal(getSafeNotesLinkUrl(renderedLinkHref(scriptTarget)), null);
  const noHref = { getAttribute: () => null };
  assert.equal(getSafeNotesLinkUrl(renderedLinkHref({ closest: () => noHref })), null);
});
