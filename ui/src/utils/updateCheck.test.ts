// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  shouldAdoptBackgroundCheck,
  shouldScanDownloadsAfterCheck, changedInfoOfFinishedRefresh,
  updateCheckAnnouncement,
  updateCheckErrorText,
  updateCheckSummary,
  lastCheckText,
  autoCheckText,
  updatesEmptyState,
} from "./updateCheck.ts";

test("a background check is adopted unless the view is already polling", () => {
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: true, checked: 0, outcome: "running" }),
    true,
  );
  // A short check can finish between the view's own status reads.
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: false, checked: 3, outcome: "completed" }),
    true,
  );
  assert.equal(
    shouldAdoptBackgroundCheck(true, { running: true, checked: 1, outcome: "running" }),
    false,
  );
  assert.equal(
    shouldAdoptBackgroundCheck(false, { running: false, checked: 0, outcome: "idle" }),
    false,
  );
});

test("download offers are scanned for a finished check even if running was missed", () => {
  const finished = { running: false, checked: 2, outcome: "completed" };
  // The normal case: the poll saw the check running, then saw it finish.
  assert.equal(shouldScanDownloadsAfterCheck(finished, true, true), true);
  // A short check started and finished between polls; the results still changed.
  assert.equal(shouldScanDownloadsAfterCheck(finished, false, true), true);
  // Nothing new to scan for once that status has been handled.
  assert.equal(shouldScanDownloadsAfterCheck(finished, false, false), false);
  // A running check or an idle/empty one is not a finished check.
  assert.equal(
    shouldScanDownloadsAfterCheck({ running: true, checked: 1, outcome: "running" }, false, true),
    false,
  );
  assert.equal(
    shouldScanDownloadsAfterCheck({ running: false, checked: 0, outcome: "idle" }, true, true),
    false,
  );
});

test("only a finished F95 info refresh reports changed games", () => {
  const results = [
    { id: 1, info_changed: true, previous: { cover_image_path: "old", rating: "4.1/5" } },
    { id: 2, info_changed: false, previous: { f95_tags: null } },
    { id: 3, info_changed: true },
  ];
  const base = { running: false, checked: 3, outcome: "completed", refresh_info: true, results };
  assert.deepEqual(changedInfoOfFinishedRefresh(base), [
    { id: 1, previous: { cover_image_path: "old", rating: "4.1/5" } },
    { id: 3, previous: {} },
  ]);
  assert.deepEqual(changedInfoOfFinishedRefresh({ ...base, outcome: "cancelled" }).length, 2);
  assert.deepEqual(changedInfoOfFinishedRefresh({ ...base, running: true, outcome: "running" }), []);
  assert.deepEqual(changedInfoOfFinishedRefresh({ ...base, refresh_info: false }), []);
  assert.deepEqual(changedInfoOfFinishedRefresh({ ...base, results: [] }), []);
});

test("the Updates page says it was not checked until a check has run", () => {
  const base = { running: false, outcome: "idle", lastCheck: "", resultCount: 0, errorCount: 0 };
  assert.equal(updatesEmptyState(base), "not-checked");
  assert.equal(updatesEmptyState({ ...base, outcome: undefined, lastCheck: null }), "not-checked");
  assert.equal(updatesEmptyState({ ...base, lastCheck: "   " }), "not-checked");
  assert.equal(updatesEmptyState({ ...base, running: true, outcome: "running" }), "checking");
});

test("the up-to-date mark needs a real check without failed games", () => {
  const base = { running: false, outcome: "idle", lastCheck: "", resultCount: 0, errorCount: 0 };
  assert.equal(updatesEmptyState({ ...base, lastCheck: "2026-11-20T10:00:00" }), "up-to-date");
  assert.equal(updatesEmptyState({ ...base, outcome: "completed", resultCount: 4 }), "up-to-date");
  // A stopped or failed check without an earlier full pass proves nothing.
  assert.equal(updatesEmptyState({ ...base, outcome: "cancelled", resultCount: 2 }), "none-found");
  assert.equal(updatesEmptyState({ ...base, outcome: "failed", resultCount: 3, errorCount: 3 }), "none-found");
  assert.equal(
    updatesEmptyState({ ...base, outcome: "completed", lastCheck: "2026-11-20T10:00:00", resultCount: 3, errorCount: 1 }),
    "none-found",
  );
  // Results without an outcome (an older backend) still count as a check.
  assert.equal(updatesEmptyState({ ...base, outcome: undefined, resultCount: 2 }), "none-found");
});

test("the update-check announcer speaks only when a check starts or ends", () => {
  const base = { total: 3, checked: 0, updates: 0, errors: 0 };
  assert.equal(updateCheckAnnouncement(false, { ...base, running: true }), "Update check started for 3 games.");
  assert.equal(
    updateCheckAnnouncement(false, { ...base, running: true, total: 1 }),
    "Update check started for 1 game.",
  );
  // A check found already running when the page opens.
  assert.equal(
    updateCheckAnnouncement(false, { ...base, running: true, checked: 2 }),
    "Update check running: 2 of 3 games checked.",
  );
  // Progress while running is not announced.
  assert.equal(updateCheckAnnouncement(true, { ...base, running: true, checked: 1 }), "");
  assert.equal(updateCheckAnnouncement(false, { ...base, running: false, outcome: "completed" }), "");
  assert.equal(
    updateCheckAnnouncement(true, { ...base, running: false, checked: 3, outcome: "completed", updates: 1, errors: 0 }),
    "Update check finished: 3 of 3 games checked. 1 update found.",
  );
  // The backend says "failed" when every game was read but some checks failed.
  assert.equal(
    updateCheckAnnouncement(true, { ...base, running: false, checked: 3, outcome: "failed", error: "Some games could not be checked; see results for details", updates: 2, errors: 1 }),
    "Update check finished: 3 of 3 games checked. 1 check failed. 2 updates found.",
  );
  assert.equal(
    updateCheckAnnouncement(true, { ...base, running: false, checked: 1, outcome: "cancelled" }),
    "Update check stopped: 1 of 3 games checked.",
  );
  assert.equal(
    updateCheckAnnouncement(true, { ...base, running: false, checked: 1, outcome: "failed", error: "F95Zone is down." }),
    "Update check failed: 1 of 3 games checked. F95Zone is down.",
  );
});

test("failed checks get plain words with what to do", () => {
  const text = (error: string, error_code = "", browser_fallback = "") =>
    updateCheckErrorText({ error, error_code, browser_fallback });
  assert.deepEqual(text("HTTP Error 429: Too Many Requests"), {
    message: "F95Zone limited the number of requests. Try again later.",
    detail: "HTTP Error 429: Too Many Requests",
  });
  assert.deepEqual(text("Rate limited by F95Zone; try again later.", "rate_limited"), {
    message: "F95Zone limited the number of requests. Try again later.",
    detail: "",
  });
  assert.equal(
    text("Rate limited by F95Zone; try again in 42 seconds; try again later.", "rate_limited").message,
    "F95Zone limited the number of requests. Try again in 42 seconds.",
  );
  assert.equal(
    text("Blocked by anti-bot challenge while loading thread. Keep a browser with the wLib extension open and try again.", "blocked", "unavailable").message,
    "F95Zone showed a browser check. Open a browser with the wLib extension and try again.",
  );
  assert.equal(
    text("Blocked by anti-bot challenge while loading thread. F95Zone is challenging HTTP requests; try again later.", "blocked").message,
    "F95Zone showed a browser check. Try again later.",
  );
  assert.equal(
    text("Login is required to access this thread. Log in to F95Zone in the browser where the wLib extension is installed.", "login_required").message,
    "This thread needs an F95Zone login. Log in to F95Zone in the browser with the wLib extension.",
  );
  assert.equal(
    text("Login is required to access this thread", "login_required", "failed").message,
    "This thread needs an F95Zone login. The wLib extension could not open the thread. Try again.",
  );
  assert.equal(
    text("Thread not found; it may have been deleted or moved", "not_found").message,
    "F95Zone thread not found. The thread was possibly deleted or moved. Check the thread link of the game.",
  );
  assert.deepEqual(text("F95Zone request timed out", "navigation_timeout"), {
    message: "F95Zone did not answer in time. Try again later.",
    detail: "",
  });
  assert.deepEqual(text("[Errno -2] Name or service not known", "scraper_error"), {
    message: "wLib could not connect to F95Zone. Check your internet connection and try again.",
    detail: "[Errno -2] Name or service not known",
  });
  assert.deepEqual(text("F95Zone's version checker returned HTTP 502. Try again later.", "server_error"), {
    message: "F95Zone has a server problem. Try again later.",
    detail: "F95Zone's version checker returned HTTP 502. Try again later.",
  });
  assert.equal(text("F95Zone returned HTTP 403", "scraper_error").detail, "F95Zone returned HTTP 403");
  assert.equal(
    text("Could not extract a version from thread", "extract_failed").message,
    "wLib could not find a version in the thread. Open the thread to see the version.",
  );
  // LewdCorner messages are already plain; they keep their words.
  assert.deepEqual(text("Sign in to LewdCorner in the browser with the wLib extension", "lc_login_required"), {
    message: "Sign in to LewdCorner in the browser with the wLib extension.",
    detail: "",
  });
  assert.equal(text("Finish the LewdCorner check in your browser", "blocked").message, "Finish the LewdCorner check in your browser.");
  // Unknown text stays as it is; nothing at all gets a generic reason.
  assert.deepEqual(text("something odd happened"), { message: "Something odd happened.", detail: "" });
  assert.equal(text("").message, "The check failed. wLib got no reason. Try again.");
});

test("the status card states the outcome of a check once", () => {
  const base = { total: 120, checked: 120, error: "", errors: 0 };
  assert.equal(updateCheckSummary({ ...base, outcome: "idle" }), null);
  assert.equal(updateCheckSummary({ ...base, outcome: "running" }), null);
  assert.deepEqual(updateCheckSummary({ ...base, outcome: "completed" }), {
    tone: "success", text: "Update check finished: 120 of 120 games checked.", reason: null, rawError: "",
  });
  assert.deepEqual(
    updateCheckSummary({ ...base, outcome: "failed", error: "Some games could not be checked; see results for details", errors: 2 }),
    { tone: "warning", text: "Update check finished: 120 of 120 games checked. 2 checks failed.", reason: null, rawError: "" },
  );
  assert.deepEqual(updateCheckSummary({ ...base, outcome: "cancelled", checked: 40 }), {
    tone: "neutral", text: "Update check stopped: 40 of 120 games checked.", reason: null, rawError: "",
  });
  const failed = updateCheckSummary({ ...base, outcome: "failed", checked: 0, error: "Rate limited by F95Zone; try again later.", errors: 120 });
  assert.equal(failed?.tone, "danger");
  assert.equal(failed?.text, "Update check failed: 0 of 120 games checked.");
  assert.equal(failed?.reason?.message, "F95Zone limited the number of requests. Try again later.");
  assert.equal(failed?.rawError, "Rate limited by F95Zone; try again later.");
  assert.equal(updateCheckSummary({ ...base, total: 1, checked: 1, outcome: "completed" })?.text, "Update check finished: 1 of 1 game checked.");
});

test("the header names the last check in relative time and labels the automatic check", () => {
  const now = Date.parse("2026-11-26T12:00:00Z");
  assert.deepEqual(lastCheckText(""), { text: "never", full: "" });
  assert.deepEqual(lastCheckText(null), { text: "never", full: "" });
  assert.equal(lastCheckText("2026-11-23T12:00:00Z", now).text, "3 days ago");
  assert.equal(lastCheckText("2026-11-26T11:59:50Z", now).text, "just now");
  assert.notEqual(lastCheckText("2026-11-23T12:00:00Z", now).full, "");
  assert.deepEqual(lastCheckText("not a date", now), { text: "not a date", full: "" });
  assert.equal(autoCheckText("weekly"), "Automatic check: every week");
  assert.equal(autoCheckText("monthly"), "Automatic check: every month");
  assert.equal(autoCheckText("off"), "Automatic check: off");
  assert.equal(autoCheckText(undefined), "Automatic check: every week");
});
