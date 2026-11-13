// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import type { DownloadFailureCode, DownloadQueueJob, DownloadQueueState, UpdateDownloadJob } from "../services/api";
import {
  addedNotice,
  chartPoints,
  clampLimitKib,
  downloadPollMs,
  moveOrder,
  runQueueCommand,
  chartSummary,
  failureInfo,
  forecastText,
  gameDownloadStatus,
  groupDownloads,
  holdText,
  jobForGame,
  pauseNeedsConfirm,
  progressText,
  removeNeedsConfirm,
  rowActions,
  sectionHeading,
  sectionOf,
  shouldAutoOpen,
  sidebarBadge,
  sourceLabel,
  stateChangeAnnouncement,
  stateText,
  totalTimeLeft,
} from "./downloadQueue.ts";

const MIB = 1024 ** 2;

const job = (over: Partial<DownloadQueueJob> = {}): DownloadQueueJob => ({
  job_id: "j1", kind: "host", game_id: 1, title: "Game", version: "1.0", state: "queued", reason: "", position: 0,
  error_code: "", error_text: "", bytes_done: 0, bytes_total: null, speed: 0, eta: null, retries: 0,
  retry_at: null, retry_in: null, created_at: 1, finished_at: null, can_continue: true,
  source: { host: "pixeldrain" }, details: {}, ...over,
});

const STATES: DownloadQueueState[] = ["preparing", "queued", "retry_wait", "held", "running", "paused", "attention", "ready"];
const CODES: DownloadFailureCode[] = [
  "network", "removed", "quota", "challenge", "invalid", "disk", "write", "sign_in",
  "extension", "check_timeout", "permission", "mirror_gone", "engine", "unknown",
];

test("every state has a section, words and actions", () => {
  const sections = STATES.map((state) => sectionOf(job({ state })));
  assert.deepEqual(sections, ["upnext", "upnext", "upnext", "upnext", "downloading", "paused", "needs", "ready"]);
  for (const state of STATES) {
    assert.ok(stateText(job({ state, error_code: state === "attention" ? "network" : "" })).length > 0, state);
  }
  assert.deepEqual(rowActions(job({ state: "running" })), ["pause", "remove"]);
  assert.deepEqual(rowActions(job({ state: "queued" })), ["now", "pause", "up", "down", "top", "remove"]);
  assert.deepEqual(rowActions(job({ state: "paused" })), ["resume", "now", "remove"]);
  assert.deepEqual(rowActions(job({ state: "ready" })), ["install", "remove"]);
  assert.deepEqual(rowActions(job({ state: "attention", error_code: "removed" })), ["another", "remove"]);
});

test("state words", () => {
  assert.equal(stateText(job({ state: "running", bytes_done: 43, bytes_total: 100 })), "Downloading 43%");
  assert.equal(stateText(job({ state: "running" })), "Downloading");
  assert.equal(stateText(job({ state: "running", kind: "torrent", details: { hint: "Finding peers" } })), "Finding peers");
  assert.equal(stateText(job({ state: "retry_wait", retry_in: 30 })), "Retrying in 30 s");
  assert.equal(stateText(job({ state: "held", reason: "battery" })), "Waiting while on battery");
  assert.equal(stateText(job({ state: "held", reason: "game" })), "Waiting while a game runs");
  assert.equal(stateText(job({ state: "paused", bytes_done: 30, bytes_total: 100 })), "Paused at 30%");
  assert.equal(stateText(job({ state: "preparing", kind: "torrent" })), "Fetching the torrent");
  assert.equal(stateText(job({ state: "attention", reason: "browser_check" })), "Finish the check in the browser window that opened");
  assert.equal(stateText(job({ state: "queued", bytes_total: 2 * MIB })), "Waiting · 2.0 MiB");
});

test("sections appear only when they hold a download, in page order", () => {
  const jobs = [
    job({ job_id: "a", state: "queued" }),
    job({ job_id: "b", state: "attention", error_code: "network" }),
    job({ job_id: "c", state: "running" }),
    job({ job_id: "d", state: "running" }),
    job({ job_id: "e", state: "retry_wait" }),
  ];
  const sections = groupDownloads(jobs);
  assert.deepEqual(sections.map((section) => section.id), ["needs", "downloading", "upnext"]);
  assert.deepEqual(sections.map((section) => section.count), [1, 2, 2]);
  assert.equal(sectionHeading(sections[1]!, { active: 2, max_active: 3 }), "Downloading · 2 · 2 of 3 slots in use");
  assert.equal(sectionHeading(sections[0]!), "Needs you · 1");
  const seeding = [{ job_id: "s" } as UpdateDownloadJob];
  const offer = { game_id: 1, game_title: "G", installed_version: "1", latest_version: "2", archive_path: "/d/g.zip", archive_version: "2", guessed_title: "G", key: "k" };
  const withExtras = groupDownloads([], seeding, [offer]);
  assert.deepEqual(withExtras.map((section) => section.id), ["ready", "seeding"]);
  assert.deepEqual(groupDownloads([]), []);
});

test("a finished torrent with an open seeding question needs the user", () => {
  const torrent = job({ kind: "torrent", state: "ready", details: { seed_state: "awaiting_answer" } });
  assert.equal(sectionOf(torrent), "needs");
  assert.deepEqual(rowActions(torrent), []);
  assert.deepEqual(sidebarBadge([torrent]), { count: 0, warning: true });
});

test("every failure code has a sentence and fitting actions", () => {
  for (const code of CODES) {
    const info = failureInfo(job({ state: "attention", error_code: code, error_text: "something went wrong" }));
    assert.ok(info.sentence.length > 10, code);
    assert.ok(info.actions.includes("remove"), code);
  }
  assert.ok(failureInfo(job({ state: "attention", error_code: "network" })).actions.includes("retry"));
  assert.ok(!failureInfo(job({ state: "attention", error_code: "removed" })).actions.includes("retry"));
  assert.ok(failureInfo(job({ state: "attention", error_code: "mirror_gone" })).actions.includes("another"));
  assert.ok(failureInfo(job({ state: "attention", kind: "torrent", error_code: "sign_in" })).actions.includes("settings"));
  assert.match(failureInfo(job({ state: "attention", error_code: "sign_in", error_text: "Sign in to F95Zone in your browser" })).sentence, /F95Zone/);
});

test("a failure says how far the download got", () => {
  const stopped = failureInfo(job({ state: "attention", error_code: "network", bytes_done: 43, bytes_total: 100 }));
  assert.equal(stopped.sentence, "The host stopped responding. It stopped at 43%.");
  assert.deepEqual(stopped.actions, ["retry", "another", "remove"]);
  const disk = failureInfo(job({ state: "attention", error_code: "disk", bytes_done: 0, bytes_total: 10 * MIB }), 2 * MIB);
  assert.match(disk.sentence, /needs 10\.0 MiB and 2\.0 MiB is free/);
  assert.match(failureInfo(job({ state: "attention", error_code: "unknown", error_text: "Odd" })).sentence, /^Odd\./);
});

test("progress text with known and unknown sizes", () => {
  const torrent = job({ kind: "torrent", state: "running", bytes_done: 50 * MIB, bytes_total: 100 * MIB, speed: MIB, eta: 50, details: { peers: 14, upload_speed: 0 } });
  assert.equal(progressText(torrent), "50.0 MiB of 100 MiB · 50% · 1.0 MiB/s · ↑ 0 B/s · under a minute left · 14 peers");
  const unknown = job({ state: "running", bytes_done: 3 * MIB, speed: MIB });
  assert.equal(progressText(unknown), "3.0 MiB · 1.0 MiB/s");
  assert.equal(progressText(job({ state: "queued" })), "");
});

test("sources", () => {
  assert.equal(sourceLabel(job({ kind: "torrent", source: { rpdl_id: 4 } })), "RPDL torrent");
  assert.equal(sourceLabel(job({ source: { host: "mega" } })), "MEGA");
  assert.equal(sourceLabel(job({ source: { host: "pixeldrain" } })), "Pixeldrain");
  assert.equal(sourceLabel(job({ source: {} })), "File host");
});

test("confirmations", () => {
  assert.equal(removeNeedsConfirm(job({ bytes_done: 0 })), false);
  assert.equal(removeNeedsConfirm(job({ bytes_done: 5 })), true);
  assert.equal(removeNeedsConfirm(job({ state: "ready" })), true);
  assert.equal(pauseNeedsConfirm(job({ state: "running", can_continue: false })), true);
  assert.equal(pauseNeedsConfirm(job({ state: "running", can_continue: true })), false);
});

test("time left counts running and waiting downloads", () => {
  const jobs = [
    job({ state: "running", bytes_done: 50, bytes_total: 100 }),
    job({ state: "queued", bytes_total: 150 }),
    job({ state: "paused", bytes_total: 1000 }),
  ];
  assert.equal(totalTimeLeft(jobs, 10), 20);
  assert.equal(totalTimeLeft(jobs, 0), null);
  assert.equal(totalTimeLeft([job({ state: "queued", bytes_total: null })], 10), null);
});

test("forecast words", () => {
  const GIB = 1024 ** 3;
  assert.deepEqual(forecastText({ needed: 41 * GIB, free: 212 * GIB, unknown: 0, fits: true, missing: 0 }),
    { text: "The downloads need 41.0 GiB; 212 GiB is free.", warning: false });
  const short = forecastText({ needed: 60 * GIB, free: 45 * GIB, unknown: 1, fits: false, missing: 15 * GIB });
  assert.equal(short.warning, true);
  assert.match(short.text, /15\.0 GiB more/);
  assert.match(short.text, /1 download has an unknown size/);
  assert.equal(forecastText({ needed: 0, free: null, unknown: 2, fits: true, missing: 0 }).text, "The downloads need 0 B. 2 downloads have an unknown size.");
});

test("sidebar badge", () => {
  assert.deepEqual(sidebarBadge([job({ state: "running" }), job({ state: "queued" })]), { count: 2, warning: false });
  assert.deepEqual(sidebarBadge([job({ state: "attention" }), job({ state: "ready" })]), { count: 1, warning: true });
  assert.deepEqual(sidebarBadge([]), { count: 0, warning: false });
});

test("game status and the job it follows", () => {
  assert.equal(gameDownloadStatus(job({ state: "running", bytes_done: 43, bytes_total: 100 })), "Downloading 43%");
  assert.equal(gameDownloadStatus(job({ state: "queued" })), "Queued");
  assert.equal(gameDownloadStatus(job({ state: "paused" })), "Paused");
  assert.equal(gameDownloadStatus(job({ state: "attention" })), "Download needs you");
  assert.equal(gameDownloadStatus(job({ state: "ready" })), "Ready to install");
  assert.equal(gameDownloadStatus(undefined), "");
  const jobs = [job({ job_id: "old", state: "ready" }), job({ job_id: "new", state: "running" }), job({ job_id: "x", game_id: 2 })];
  assert.equal(jobForGame(jobs, 1)?.job_id, "new");
  assert.equal(jobForGame(jobs, 3), undefined);
});

test("the install dialog opens by itself only in the four right cases", () => {
  const done = job({ job_id: "done", state: "ready" });
  // Only download, game not running, no dialog open: opens.
  assert.equal(shouldAutoOpen(done, [done], [], false), true);
  // Another download is unfinished.
  assert.equal(shouldAutoOpen(done, [done, job({ job_id: "other", game_id: 2, state: "queued" })], [], false), false);
  // A finished one beside it does not count.
  assert.equal(shouldAutoOpen(done, [done, job({ job_id: "other", game_id: 2, state: "ready" })], [], false), true);
  // The game runs.
  assert.equal(shouldAutoOpen(done, [done], [1], false), false);
  // An install dialog is open.
  assert.equal(shouldAutoOpen(done, [done], [], true), false);
  assert.equal(shouldAutoOpen(job({ state: "running" }), [], [], false), false);
});

test("hold words", () => {
  assert.match(holdText("game"), /when the game closes/);
  assert.match(holdText("battery"), /mains power/);
  assert.equal(holdText(""), "");
});

test("chart points and text alternative", () => {
  assert.equal(chartPoints([0, 10], 100, 20), "0.0,20.0 100.0,0.0");
  assert.equal(chartPoints([5], 100, 20), "");
  assert.match(chartSummary([MIB, 3 * MIB]), /peak 3\.0 MiB\/s/);
  assert.equal(chartSummary([]), "No speed samples yet.");
});

test("state change announcements", () => {
  assert.equal(stateChangeAnnouncement(job({ state: "queued" }), job({ state: "running" })), "Game is downloading.");
  assert.equal(stateChangeAnnouncement(job({ state: "running" }), job({ state: "running" })), "");
  assert.equal(stateChangeAnnouncement(undefined, job({ state: "ready" })), "");
  assert.equal(stateChangeAnnouncement(job({ state: "running" }), job({ state: "attention" })), "Game needs you.");
});

test("poll rate follows the queue", async () => {
  const { downloadPollMs } = await import("./downloadQueue.ts");
  assert.equal(downloadPollMs([job({ state: "running" })]), 500);
  assert.equal(downloadPollMs([job({ state: "preparing" })]), 500);
  assert.equal(downloadPollMs([job({ state: "attention", reason: "browser_check" })]), 500);
  assert.equal(downloadPollMs([job({ state: "queued" })]), 2000);
  assert.equal(downloadPollMs([job({ state: "paused" })]), 2000);
  assert.equal(downloadPollMs([job({ state: "ready" })]), 10_000);
  assert.equal(downloadPollMs([]), 10_000);
});

test("a command refreshes the list and reports a refusal", async () => {
  const { runQueueCommand } = await import("./downloadQueue.ts");
  const refreshed: number[] = [];
  const reports: string[] = [];
  assert.equal(await runQueueCommand(async () => ({ success: true }), () => { refreshed.push(1); }, (m) => reports.push(m)), true);
  assert.equal(await runQueueCommand(async () => ({ success: false, error: "The download was not found" }), () => { refreshed.push(2); }, (m) => reports.push(m)), false);
  assert.equal(await runQueueCommand(async () => { throw new Error("offline"); }, () => { refreshed.push(3); }, (m) => reports.push(m)), false);
  assert.deepEqual(refreshed, [1, 2, 3]);
  assert.deepEqual(reports, ["The download was not found", "offline"]);
});

test("moving a waiting download keeps the other rows in their slots", () => {
  const all = ["run", "a", "b", "c", "paused"];
  const waiting = ["a", "b", "c"];
  assert.deepEqual(moveOrder(all, waiting, "c", 0), ["run", "c", "a", "b", "paused"]);
  assert.deepEqual(moveOrder(all, waiting, "a", 1), ["run", "b", "a", "c", "paused"]);
  assert.deepEqual(moveOrder(all, waiting, "b", 9), ["run", "a", "c", "b", "paused"]);
  assert.deepEqual(moveOrder(all, waiting, "run", 0), all);
});

test("the speed limit field accepts whole KiB/s and 0 for none", () => {
  assert.equal(clampLimitKib(512.4), 512);
  assert.equal(clampLimitKib(-5), 0);
  assert.equal(clampLimitKib("abc"), 0);
  assert.equal(clampLimitKib(1e12), 10_485_760);
});

test("the added notice says when the download waits for the game", () => {
  assert.equal(addedNotice("Eternum", false), "Eternum was added to Downloads.");
  assert.match(addedNotice("Eternum", true), /starts when the game closes/);
});

test("poll rate: fast while bytes move, slower while waiting, slow when idle", () => {
  assert.equal(downloadPollMs([job({ state: "running" })]), 500);
  assert.equal(downloadPollMs([job({ state: "queued" })]), 2000);
  assert.equal(downloadPollMs([job({ state: "ready" })]), 10_000);
  assert.equal(downloadPollMs([]), 10_000);
});

test("a queue command refreshes the list and reports a refusal", async () => {
  let refreshed = 0;
  const reports: string[] = [];
  const ok = await runQueueCommand(async () => ({ success: false, error: "Not found" }), () => { refreshed++; }, (m) => reports.push(m));
  assert.equal(ok, false);
  assert.equal(refreshed, 1);
  assert.deepEqual(reports, ["Not found"]);
  assert.equal(await runQueueCommand(async () => ({ success: true }), () => { refreshed++; }, (m) => reports.push(m)), true);
  assert.equal(refreshed, 2);
});
