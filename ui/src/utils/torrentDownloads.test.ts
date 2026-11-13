// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formatEta, formatSize, formatUploadDate, isSeeding, needsSeedAnswer, rpdlMarks, rpdlNote, rpdlPreselected,
  rpdlPrimaryAction, rpdlPrimaryLabel, torrentSentMessage, seedingLabel, seedPromptText, torrentDetails, torrentInstallSource, torrentPercent, torrentStatusLine,
} from "./torrentDownloads.ts";
import { awaitsSeedAnswer, groupDownloads, rowActions, sidebarBadge } from "./downloadQueue.ts";
import type { DownloadQueueJob, RpdlSearchResponse, RpdlTorrent, UpdateDownloadJob } from "../services/api.ts";

const job = (extra: Partial<UpdateDownloadJob> = {}): UpdateDownloadJob => ({
  job_id: "j", game_id: 1, stage: "downloading", bytes: 300, total: 1000, speed: 2048, error: "", archive_path: "",
  kind: "torrent", title: "Eternum", peers: 4, upload_speed: 1024, eta: 125, hint: "", ...extra,
});
const torrent = (extra: Partial<RpdlTorrent> = {}): RpdlTorrent => ({
  id: 1, title: "Eternum-0.8-pc", size: 3 * 1024 ** 3, seeders: 5, leechers: 1, date: 1_767_225_600, score: 95,
  version_match: true, no_seeders: false, ...extra,
});

test("sizes, times and dates read well", () => {
  assert.equal(formatSize(0), "0 B");
  assert.equal(formatSize(1536), "1.5 KiB");
  assert.equal(formatSize(3 * 1024 ** 3), "3.0 GiB");
  assert.equal(formatEta(30), "under a minute");
  assert.equal(formatEta(125), "2 min");
  assert.equal(formatEta(3900), "1 h 5 min");
  assert.equal(formatEta(null), "");
  assert.equal(formatUploadDate(1_767_225_600), "2026-01-01");
  assert.equal(formatUploadDate(0), "");
});

test("a downloading torrent shows percent, speeds, peers and time left", () => {
  assert.equal(torrentPercent(job()), 30);
  assert.equal(torrentStatusLine(job()), "Downloading 30%");
  assert.equal(torrentDetails(job()), "↓ 2.0 KiB/s · ↑ 1.0 KiB/s · 4 peers · 2 min left");
  assert.equal(torrentDetails(job({ peers: 1, eta: null })), "↓ 2.0 KiB/s · ↑ 1.0 KiB/s · 1 peer");
});

test("waiting states say what the torrent waits for", () => {
  assert.equal(torrentStatusLine(job({ stage: "fetching" })), "Fetching the torrent…");
  assert.equal(torrentStatusLine(job({ hint: "Looking for peers", peers: 0 })), "Looking for peers");
  assert.equal(torrentStatusLine(job({ stage: "paused" })), "Paused");
  assert.equal(torrentStatusLine(job({ stage: "paused", error: "Free disk space, then resume" })), "Free disk space, then resume");
  assert.equal(torrentStatusLine(job({ stage: "failed", error: "Torrent engine keeps stopping" })), "Torrent engine keeps stopping");
  assert.equal(torrentDetails(job({ stage: "paused" })), "");
});

test("seeding and the seeding question", () => {
  const seeding = job({ stage: "done", seed_state: "seeding", ratio: 0.42, ratio_target: 1, upload_speed: 122_880 });
  assert.ok(isSeeding(seeding) && !needsSeedAnswer(seeding));
  assert.equal(seedingLabel(seeding), "Seeding · ratio 0.4 of 1.0 · ↑ 120 KiB/s");
  const asking = job({ stage: "done", seed_state: "awaiting_answer", ratio_target: 1, payload_kind: "archive" });
  assert.ok(needsSeedAnswer(asking));
  const prompt = seedPromptText(asking);
  assert.equal(prompt.message, "Keep seeding Eternum until ratio 1.0?");
  assert.equal(prompt.confirmLabel, "Seed");
  assert.equal(prompt.cancelLabel, "Don't seed");
  assert.equal(prompt.checkboxLabel, "Don't ask again");
  const folder = seedPromptText(job({ stage: "done", seed_state: "awaiting_answer", ratio_target: 1, payload_kind: "folder", total: 3 * 1024 ** 3 }));
  assert.match(folder.message, /second 3\.0 GiB copy of the game until seeding ends/);
  assert.doesNotMatch(prompt.message, /second/);
});

test("the install source is the archive, else the game folder", () => {
  assert.deepEqual(torrentInstallSource(job({ archive_path: "/t/a.zip" })), { archivePath: "/t/a.zip", sourceKind: "archive" });
  assert.deepEqual(torrentInstallSource(job({ folder_path: "/t/Game" })), { archivePath: "/t/Game", sourceKind: "folder" });
  assert.equal(torrentInstallSource(job()), null);
});

test("RPDL rows are marked and only a signed-in clear match is preselected", () => {
  assert.deepEqual(rpdlMarks(torrent()).map((m) => m.text), ["Matches version"]);
  assert.deepEqual(rpdlMarks(torrent({ version_match: false, no_seeders: true })).map((m) => m.text), ["Different version", "No seeders"]);
  assert.deepEqual(rpdlMarks(torrent({ version_match: null })), []);
  const response: RpdlSearchResponse = { success: true, results: [torrent(), torrent({ id: 2 })], preselected_id: 1, needs_choice: false, signed_in: true };
  assert.equal(rpdlPreselected(response)?.id, 1);
  assert.equal(rpdlPreselected({ ...response, signed_in: false }), null);
  assert.equal(rpdlPreselected({ ...response, preselected_id: null }), null);
  assert.equal(rpdlPreselected(null), null);
});

test("the RPDL note asks to sign in or to choose", () => {
  const base: RpdlSearchResponse = { success: true, results: [torrent()], preselected_id: null, needs_choice: true, signed_in: true };
  assert.match(rpdlNote({ ...base, signed_in: false }), /Sign in to RPDL in Settings/);
  assert.match(rpdlNote(base), /Choose one/);
  assert.equal(rpdlNote({ ...base, needs_choice: false }), "");
  assert.equal(rpdlNote({ success: true, results: [] }), "");
});

test("primary RPDL action follows the watch folder", () => {
  assert.equal(rpdlPrimaryAction(""), "download");
  assert.equal(rpdlPrimaryAction("   "), "download");
  assert.equal(rpdlPrimaryAction(undefined), "download");
  assert.equal(rpdlPrimaryAction("/home/kim/watch"), "send");
  assert.equal(rpdlPrimaryLabel("send"), "Send to client");
  assert.equal(rpdlPrimaryLabel("download"), "Download");
});

test("sent message names the folder and the archive install", () => {
  const text = torrentSentMessage(" /home/kim/watch ");
  assert.match(text, /\(\/home\/kim\/watch\)/);
  assert.match(text, /Install from archive/);
  assert.match(torrentSentMessage(""), /^Sent to the watch folder\./);
});

const finishedTorrent = (seed_state: string): DownloadQueueJob => ({
  job_id: "t1", kind: "torrent", game_id: 1, title: "Eternum", version: "1.0", state: "ready", reason: "", position: 0,
  error_code: "", error_text: "", bytes_done: 1000, bytes_total: 1000, speed: 0, eta: null, retries: 0,
  retry_at: null, retry_in: null, created_at: 1, finished_at: 2, can_continue: true,
  source: { rpdl_id: 7 }, details: { seed_state, archive_path: "/a.zip" },
});

test("a finished torrent with an open seeding question waits under Needs you", () => {
  const asking = finishedTorrent("awaiting_answer");
  assert.equal(awaitsSeedAnswer(asking), true);
  assert.deepEqual(rowActions(asking), []);
  assert.equal(groupDownloads([asking])[0]?.id, "needs");
  assert.equal(sidebarBadge([asking]).warning, true);
  const answered = finishedTorrent("seeding");
  assert.equal(awaitsSeedAnswer(answered), false);
  assert.equal(groupDownloads([answered])[0]?.id, "ready");
});

test("seeding torrents get their own section after Ready to install", () => {
  const sections = groupDownloads([finishedTorrent("seeding")], [job({ stage: "done", seed_state: "seeding" })]);
  assert.deepEqual(sections.map((s) => s.id), ["ready", "seeding"]);
  assert.equal(sections[1]?.seeding.length, 1);
});
