// SPDX-License-Identifier: GPL-3.0-or-later
import test, { mock } from "node:test";
import assert from "node:assert/strict";

import type { FfmpegInfo, WebMediaStatus } from "../services/api.ts";
import {
  canConvert,
  createWebMediaWatcher,
  ffmpegInstallHint,
  ffmpegProblem,
  initialStep,
  progressText,
  summaryText,
  unsupportedText,
} from "./webMedia.ts";

const info = (fields: Partial<FfmpegInfo> = {}): FfmpegInfo => ({
  available: true,
  ffmpeg: "/usr/bin/ffmpeg",
  ffprobe: "/usr/bin/ffprobe",
  video_encoder: "libsvtav1",
  audio_encoder: "libopus",
  reason: "",
  install: { command: "sudo pacman -S ffmpeg", package_manager: "pacman", platform: "linux", host: false },
  ...fields,
});

const status = (fields: Partial<WebMediaStatus> = {}): WebMediaStatus => ({
  success: true,
  running: true,
  finished: false,
  done: 0,
  total: 4,
  current: "",
  converted: 0,
  failed: 0,
  failed_files: [],
  cancelled: false,
  error: "",
  ...fields,
});

test("dialog state and texts", () => {
  assert.equal(initialStep("web_media_unsupported"), "choices");
  assert.equal(initialStep("web_media_converting"), "progress");
  assert.equal(unsupportedText(1609), "1609 videos use a format the built-in player cannot play (H.264, H.265 or AAC).");
  assert.equal(canConvert(info()), true);
  assert.equal(ffmpegProblem(info()), "");
  const missing = info({ available: false, reason: "missing" });
  assert.equal(canConvert(missing), false);
  assert.match(ffmpegProblem(missing), /needs ffmpeg/);
  assert.match(ffmpegProblem(info({ available: false, reason: "no_video_encoder" })), /AV1 or VP9/);
  assert.deepEqual(ffmpegInstallHint(missing), { note: "Install ffmpeg with this command:", command: "sudo pacman -S ffmpeg" });
  const windows = info({ install: { command: "winget install Gyan.FFmpeg", package_manager: "winget", platform: "windows", host: false } });
  assert.equal(ffmpegInstallHint(windows).command, "winget install Gyan.FFmpeg");
  const flatpak = info({ install: { command: "sudo apt install ffmpeg", package_manager: "apt", platform: "linux", host: true } });
  assert.match(ffmpegInstallHint(flatpak).note, /outside the Flatpak/);
  assert.equal(progressText(status({ done: 2 })), "2 of 4 videos");
});

test("summary text", () => {
  assert.deepEqual(summaryText(status({ running: false, finished: true, converted: 4 })), { text: "4 videos converted.", type: "success" });
  assert.deepEqual(summaryText(status({ converted: 3, failed: 1 })), { text: "3 videos converted, 1 could not be converted.", type: "error" });
  assert.equal(summaryText(status({ converted: 1, cancelled: true })).text, "Conversion stopped. 1 video converted.");
  assert.equal(summaryText(status({ error: "Disk full." })).type, "error");
});

test("the watcher polls until the job ends, then reports once", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const answers = [status({ done: 1 }), status({ done: 3 }), status({ running: false, finished: true, done: 4, converted: 4 })];
    const updates: number[] = [];
    const finished: WebMediaStatus[] = [];
    let calls = 0;
    const watcher = createWebMediaWatcher({
      getStatus: async () => answers[Math.min(calls++, answers.length - 1)]!,
      onUpdate: (_id, value) => updates.push(value.done),
      onFinish: (_id, value) => finished.push(value),
    });
    watcher.watch(7);
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(updates, [1]);
    assert.equal(watcher.watching(7), true);
    mock.timers.tick(500);
    await Promise.resolve();
    await Promise.resolve();
    mock.timers.tick(500);
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(updates, [1, 3, 4]);
    assert.equal(finished.length, 1);
    assert.equal(watcher.watching(7), false);
    mock.timers.tick(5000);
    await Promise.resolve();
    assert.equal(calls, 3);
  } finally {
    mock.timers.reset();
  }
});

test("an idle status stops the watcher without a notification", async () => {
  const finished: WebMediaStatus[] = [];
  const watcher = createWebMediaWatcher({
    getStatus: async () => status({ running: false, total: 0 }),
    onUpdate: () => {},
    onFinish: (_id, value) => finished.push(value),
  });
  watcher.watch(3);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(watcher.watching(3), false);
  assert.equal(finished.length, 0);
});
