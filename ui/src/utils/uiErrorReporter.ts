// SPDX-License-Identifier: GPL-3.0-or-later
import type { UiErrorReport } from "../services/api.ts";

const DEDUPE_MS = 10_000;
const RATE_WINDOW_MS = 60_000;
const REPORT_LIMIT = 20;

type Send = (report: UiErrorReport) => unknown;
interface ReporterOptions {
  now?: () => number;
  schedule?: (callback: () => void, delay: number) => void;
}

function errorDetails(reason: unknown, fallback = reason): Pick<UiErrorReport, "message" | "stack"> {
  const error = reason as { message?: unknown; stack?: unknown } | null | undefined;
  return {
    message: String(error?.message ?? fallback),
    stack: String(error?.stack ?? ""),
  };
}

export function createUiErrorReporter({
  now = () => performance.now(),
  schedule = (callback, delay) => { setTimeout(callback, delay); },
}: ReporterOptions = {}) {
  const pending: UiErrorReport[] = [];
  const sentAt: number[] = [];
  const recent = new Map<string, { count: number; until: number }>();
  let send: Send | undefined;
  let dropped = 0;
  let reporting = false;

  function guarded(work: () => void): void {
    if (reporting) return;
    reporting = true;
    try {
      work();
    } catch {
      // Report construction and delivery must stay out of the UI error handlers.
    } finally {
      reporting = false;
    }
  }

  function submit(report: UiErrorReport): boolean {
    if (!send) {
      if (pending.length < REPORT_LIMIT) {
        pending.push(report);
        return true;
      }
    } else {
      const time = now();
      while (sentAt.length && sentAt[0]! <= time - RATE_WINDOW_MS) sentAt.shift();
      if (sentAt.length < REPORT_LIMIT) {
        sentAt.push(time);
        const payload = { ...report, dropped };
        dropped = 0;
        try {
          void Promise.resolve(send(payload)).catch(() => {});
        } catch {
          // A synchronous bridge failure is also best effort.
        }
        return true;
      }
    }
    dropped++;
    return false;
  }

  function capture(report: UiErrorReport): void {
    const firstLine = report.stack.split(/\r?\n/, 1)[0];
    const key = JSON.stringify([report.source, report.message, firstLine]);
    const time = now();
    const previous = recent.get(key);
    if (previous && time < previous.until) {
      previous.count++;
      return;
    }
    if (!submit(report)) return;

    const occurrence = { count: 1, until: time + DEDUPE_MS };
    recent.set(key, occurrence);
    schedule(() => guarded(() => {
      if (recent.get(key) === occurrence) recent.delete(key);
      if (occurrence.count > 1) submit({ ...report, count: occurrence.count });
    }), DEDUPE_MS);
  }

  return {
    connect(bridgeSend: Send): void {
      guarded(() => {
        send = bridgeSend;
        for (const report of pending.splice(0)) submit(report);
      });
    },
    reportVue(error: unknown, where: string, route: string): void {
      guarded(() => capture({
        ...errorDetails(error), source: "vue", where, route, count: 1, dropped: 0,
      }));
    },
    reportWindow(event: ErrorEvent, route: string): void {
      guarded(() => {
        if (event.error == null && event.message === "Script error.") return;
        capture({
          ...errorDetails(event.error, event.message),
          source: "window",
          where: event.filename ? `${event.filename}:${event.lineno}:${event.colno}` : "window",
          route,
          count: 1,
          dropped: 0,
        });
      });
    },
    reportRejection(event: PromiseRejectionEvent, route: string): void {
      guarded(() => capture({
        ...errorDetails(event.reason), source: "promise", where: "promise", route, count: 1, dropped: 0,
      }));
    },
  };
}
