// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import type { UiErrorReport } from "../services/api.ts";
import { createUiErrorReporter } from "./uiErrorReporter.ts";

function setup() {
  let time = 0;
  const scheduled: { due: number; callback: () => void }[] = [];
  const reports: UiErrorReport[] = [];
  const reporter = createUiErrorReporter({
    now: () => time,
    schedule: (callback, delay) => scheduled.push({ due: time + delay, callback }),
  });
  return {
    reporter,
    reports,
    connect: () => reporter.connect((report) => reports.push(report)),
    advance(ms: number) {
      const target = time + ms;
      while (scheduled[0] && scheduled[0].due <= target) {
        const task = scheduled.shift()!;
        time = task.due;
        task.callback();
      }
      time = target;
    },
  };
}

function rejection(reason: unknown): PromiseRejectionEvent {
  return { reason } as PromiseRejectionEvent;
}

test("captures Vue errors, window errors and non-Error rejection reasons", () => {
  const { reporter, reports, connect } = setup();
  connect();
  const error = new Error("failed");
  reporter.reportVue(error, "GameCard / render", "/library");
  reporter.reportWindow({
    error, filename: "http://localhost/app.js", lineno: 2, colno: 3,
  } as ErrorEvent, "/library");
  reporter.reportWindow({ message: "Script error.", error: null } as ErrorEvent, "/library");
  reporter.reportWindow({ message: "window fallback", error: null } as ErrorEvent, "/library");
  reporter.reportRejection(rejection("offline"), "/sync");
  reporter.reportRejection(rejection({ code: 42 }), "/sync");
  reporter.reportRejection(rejection({ message: "bad request", stack: "some stack" }), "/sync");
  assert.deepEqual(reports, [
    { source: "vue", message: "failed", stack: error.stack, where: "GameCard / render", route: "/library", count: 1, dropped: 0 },
    { source: "window", message: "failed", stack: error.stack, where: "http://localhost/app.js:2:3", route: "/library", count: 1, dropped: 0 },
    { source: "window", message: "window fallback", stack: "", where: "window", route: "/library", count: 1, dropped: 0 },
    { source: "promise", message: "offline", stack: "", where: "promise", route: "/sync", count: 1, dropped: 0 },
    { source: "promise", message: "[object Object]", stack: "", where: "promise", route: "/sync", count: 1, dropped: 0 },
    { source: "promise", message: "bad request", stack: "some stack", where: "promise", route: "/sync", count: 1, dropped: 0 },
  ]);
});

test("summarizes repeats after ten seconds and starts a new window", () => {
  const { reporter, reports, connect, advance } = setup();
  connect();
  const error = { message: "loop", stack: "top\nfirst location" };
  reporter.reportVue(error, "GameCard / render", "/library");
  advance(2_000);
  for (let i = 1; i < 500; i++) {
    reporter.reportVue({ ...error, stack: "top\nanother location" }, "GameCard / render", "/library");
  }
  reporter.reportVue({ ...error, stack: "another top" }, "GameCard / render", "/library");
  reporter.reportVue({ ...error, message: "another message" }, "GameCard / render", "/library");
  reporter.reportRejection(rejection(error), "/library");
  advance(7_999);
  assert.deepEqual(reports.map(({ count }) => count), [1, 1, 1, 1]);
  advance(1);
  assert.deepEqual(reports.map(({ count }) => count), [1, 1, 1, 1, 500]);
  reporter.reportVue(error, "GameCard / render", "/library");
  assert.equal(reports.at(-1)?.count, 1);
});

test("keeps twenty startup reports in order and counts queue overflow", () => {
  const { reporter, reports, connect } = setup();
  for (let i = 0; i < 22; i++) reporter.reportRejection(rejection(i), "/");
  assert.equal(reports.length, 0);
  connect();
  assert.deepEqual(reports.map(({ message }) => message), Array.from({ length: 20 }, (_, i) => String(i)));
  assert.equal(reports[0]?.dropped, 2);
  assert.equal(reports[1]?.dropped, 0);
});

test("limits sends in a sliding minute and attaches the dropped count to the next send", () => {
  const { reporter, reports, connect, advance } = setup();
  connect();
  const report = (reason: unknown) => reporter.reportRejection(rejection(reason), "/");
  report(0);
  advance(20_000);
  for (let i = 1; i < 50; i++) report(i);
  advance(39_999);
  report("still limited");
  assert.equal(reports.length, 20);
  advance(1);
  report("one slot");
  report("still limited");
  assert.equal(reports.length, 21);
  assert.equal(reports[20]?.dropped, 31);
  advance(20_000);
  report("next slot");
  assert.equal(reports[21]?.dropped, 1);
});

test("guards reentrant errors and absorbs synchronous and rejected sends", async () => {
  const { reporter, reports } = setup();
  reporter.connect((report) => {
    reports.push(report);
    reporter.reportRejection(rejection("reentrant"), "/");
    if (report.message === "sync") throw new Error("sync bridge failure");
    if (report.message === "async") return Promise.reject(new Error("async bridge failure"));
  });
  reporter.reportRejection(rejection("sync"), "/");
  reporter.reportRejection(rejection("async"), "/");
  await new Promise((resolve) => setImmediate(resolve));
  reporter.reportRejection(rejection("next"), "/");
  assert.deepEqual(reports.map(({ message }) => message), ["sync", "async", "next"]);
});
