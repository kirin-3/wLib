// SPDX-License-Identifier: GPL-3.0-or-later

import type { RefreshInfoPrevious } from "../services/api";
import { formatRelativeAge } from "./storage.ts";

// Shared rules for reading the bulk update-check status in the Updates view and
// the global poll in App.vue. The backend reports `outcome` as a free string;
// only these values are meaningful to the UI.
export type UpdateCheckOutcome = "idle" | "running" | "completed" | "cancelled" | "failed";

export interface UpdateCheckStatusLike {
  running: boolean;
  checked: number;
  outcome?: string | null;
}

// A check can be started by an automatic check or an external import while the
// Updates view is already open. Its local poll only starts when the view sees a
// running status on mount, so adopt one found by a later refresh.
export const shouldAdoptBackgroundCheck = (
  currentRunning: boolean,
  fetched: UpdateCheckStatusLike,
): boolean =>
  !currentRunning &&
  (fetched.running ||
    (fetched.checked > 0 && !!fetched.outcome && fetched.outcome !== "idle"));

// App.vue polls every five seconds. A short check can start and finish between
// two polls, so waiting to observe `running` before scanning the Downloads
// folder skipped the post-check offer. A finished status with results is enough;
// scanDownloadOffers() still enforces the once-per-minute limit.
export const shouldScanDownloadsAfterCheck = (
  status: UpdateCheckStatusLike,
  wasRunning: boolean,
  progressChanged: boolean,
): boolean =>
  !status.running &&
  status.checked > 0 &&
  !!status.outcome &&
  status.outcome !== "idle" &&
  (wasRunning || progressChanged);

export interface InfoRefreshStatusLike extends UpdateCheckStatusLike {
  refresh_info?: boolean | undefined;
  results: { id: number; info_changed?: boolean | undefined; previous?: RefreshInfoPrevious | undefined }[];
}

// Games whose F95 info (cover, rating or tags) a finished "Refresh F95 info"
// run changed, with the old values of the changed fields. Cancelled runs count
// too: the changes already made stay, so the user can still undo them.
export const changedInfoOfFinishedRefresh = (
  status: InfoRefreshStatusLike,
): { id: number; previous: RefreshInfoPrevious }[] =>
  status.refresh_info && !status.running && status.outcome && status.outcome !== "idle"
    ? status.results
        .filter((result) => result.info_changed)
        .map((result) => ({ id: result.id, previous: result.previous ?? {} }))
    : [];

// What the Updates page shows when it has no update rows. "not-checked" is the
// first-run state (no check ever finished, none running, no results): it
// explains what a check does instead of claiming there are no updates. The
// green "up-to-date" mark needs a real check: a recorded full pass or a check
// that completed in this session, with no failed games in the current results.
export type UpdatesEmptyState = "not-checked" | "checking" | "up-to-date" | "none-found";

export interface UpdatesEmptyStateInput {
  running: boolean;
  outcome?: string | null | undefined;
  lastCheck?: string | null | undefined;
  resultCount: number;
  errorCount: number;
}

export const updatesEmptyState = ({
  running,
  outcome,
  lastCheck,
  resultCount,
  errorCount,
}: UpdatesEmptyStateInput): UpdatesEmptyState => {
  if (running) return "checking";
  const recorded = !!lastCheck?.trim();
  const finishedHere = !!outcome && outcome !== "idle" && outcome !== "running";
  if (!recorded && !finishedHere && resultCount === 0) return "not-checked";
  if ((recorded || outcome === "completed") && errorCount === 0) return "up-to-date";
  return "none-found";
};

// Failed checks carry the backend's message and, mostly, a code (see
// core/scraper.py `_classify_page_issue` and `_urllib_f95_get`,
// `ApiBase._build_scraper_error_payload`, and LC_UPDATE_ERRORS in
// core/lewdcorner.py). Show plain words that say what to do. `detail` keeps
// technical text (an HTTP status, a network error, an exception) visible for
// bug reports; the view also puts the raw text in a title.
export interface UpdateCheckErrorInput {
  error?: string | null | undefined;
  error_code?: string | null | undefined;
  browser_fallback?: string | null | undefined;
}

export interface UpdateCheckErrorText {
  message: string;
  detail: string;
}

const sentence = (text: string): string => {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const capital = trimmed[0]!.toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
};

const NETWORK_ERROR =
  /name or service not known|temporary failure in name resolution|getaddrinfo|nodename nor servname|network is unreachable|no route to host|connection (?:refused|reset|aborted)|remote end closed|urlopen error|certificate|ssl|eof occurred/i;
const EXTENSION_LOGIN = "Log in to F95Zone in the browser with the wLib extension.";

export const updateCheckErrorText = ({
  error,
  error_code,
  browser_fallback,
}: UpdateCheckErrorInput): UpdateCheckErrorText => {
  const raw = (error ?? "").trim();
  const code = (error_code ?? "").trim();
  const status = Number(raw.match(/\bHTTP(?: Error)? (\d{3})\b/i)?.[1] ?? 0);
  const plain = (message: string): UpdateCheckErrorText => ({ message, detail: "" });
  const technical = (message: string): UpdateCheckErrorText => ({ message, detail: raw });

  if (!raw && !code) return plain("The check failed. wLib got no reason. Try again.");
  // LewdCorner messages are already plain and say what to do.
  if (code.startsWith("lc_") || /lewdcorner/i.test(raw)) return plain(sentence(raw) || "The LewdCorner check failed. Try again.");

  if (code === "rate_limited" || status === 429 || /rate limit|too many requests/i.test(raw)) {
    const wait = raw.match(/try again in (\d+) seconds?/i)?.[1];
    const message = `F95Zone limited the number of requests. Try again ${wait ? `in ${wait} seconds` : "later"}.`;
    return status ? technical(message) : plain(message);
  }
  if (code === "blocked" || /anti-bot|challenge|cloudflare|just a moment/i.test(raw)) {
    const intro = "F95Zone showed a browser check.";
    if (browser_fallback === "unavailable") return plain(`${intro} Open a browser with the wLib extension and try again.`);
    if (browser_fallback === "failed") return plain(`${intro} Open F95Zone in the browser with the wLib extension, complete the check, and try again.`);
    return plain(`${intro} Try again later.`);
  }
  if (code === "login_required" || /login is required|must be logged.in|sign in to f95zone/i.test(raw)) {
    const intro = "This thread needs an F95Zone login.";
    if (browser_fallback === "unavailable") return plain(`${intro} Open the browser with the wLib extension and try again.`);
    if (browser_fallback === "failed") return plain(`${intro} The wLib extension could not open the thread. Try again.`);
    return plain(`${intro} ${EXTENSION_LOGIN}`);
  }
  if (code === "not_found" || status === 404 || /thread not found|could not be found/i.test(raw)) {
    return plain("F95Zone thread not found. The thread was possibly deleted or moved. Check the thread link of the game.");
  }
  if (status === 403) return technical(`F95Zone refused the request. ${EXTENSION_LOGIN} Then try again.`);
  if (code === "navigation_timeout" || /timed out|timeout/i.test(raw)) {
    return plain("F95Zone did not answer in time. Try again later.");
  }
  if (code === "server_error" || status >= 500) return technical("F95Zone has a server problem. Try again later.");
  if (code === "extract_failed") return plain("wLib could not find a version in the thread. Open the thread to see the version.");
  if (code === "db_write_failed") return technical("wLib could not save the result. Try again.");
  if (/unexpected reply/i.test(raw)) return plain("F95Zone sent an answer that wLib cannot read. Try again later.");
  if (NETWORK_ERROR.test(raw)) return technical("wLib could not connect to F95Zone. Check your internet connection and try again.");
  if (/^update check interrupted/i.test(raw)) return plain("The update check stopped before the end. Try again.");
  if (/^update check failed/i.test(raw)) {
    return { message: "The update check stopped because of an error. Try again.", detail: raw.replace(/^update check failed(?: to start)?:\s*/i, "") };
  }
  if (/^some games could not be checked/i.test(raw)) return plain("Some games could not be checked. See the failed checks below.");
  if (/^batch update check failed/i.test(raw)) return plain("F95Zone did not answer the check. Try again later.");
  if (status) return technical(`F95Zone returned an error. Try again later.`);
  return plain(sentence(raw));
};

// The one statement the status card shows after a check, and the start of
// what the announcer says, so both agree. The backend reports "failed" also
// when every game was read but some checks failed; that is a finished check
// with failed checks, not a failed check.
export type UpdateCheckTone = "success" | "warning" | "danger" | "neutral";

export interface UpdateCheckSummaryInput {
  outcome?: string | null | undefined;
  total: number;
  checked: number;
  error?: string | null | undefined;
  errors: number;
}

export interface UpdateCheckSummary {
  tone: UpdateCheckTone;
  text: string;
  // Plain reason of a failed check, with the raw backend text for a title.
  reason: UpdateCheckErrorText | null;
  rawError: string;
}

const countOf = (count: number, one: string, many: string): string => `${count} ${count === 1 ? one : many}`;

export const updateCheckSummary = ({
  outcome,
  total,
  checked,
  error,
  errors,
}: UpdateCheckSummaryInput): UpdateCheckSummary | null => {
  if (!outcome || outcome === "idle" || outcome === "running") return null;
  const progress = `${checked} of ${countOf(total, "game", "games")} checked`;
  const failedChecks = `${countOf(errors, "check", "checks")} failed.`;
  const none = { reason: null, rawError: "" };
  if (outcome === "cancelled") return { tone: "neutral", text: `Update check stopped: ${progress}.`, ...none };
  const ranThrough = total > 0 && checked >= total;
  if (outcome === "completed" && !errors) return { tone: "success", text: `Update check finished: ${progress}.`, ...none };
  if (ranThrough && errors > 0) return { tone: "warning", text: `Update check finished: ${progress}. ${failedChecks}`, ...none };
  const rawError = (error ?? "").trim();
  return {
    tone: "danger",
    text: `Update check failed: ${progress}.`,
    reason: rawError ? updateCheckErrorText({ error: rawError }) : null,
    rawError,
  };
};

// What the Updates page's screen reader announcer says when a check starts or
// ends. The per-game "Checking: X" line changes every few seconds and is not
// announced; only these transitions are. Empty when nothing changed. The end
// message is the status card's statement plus the number of updates found.
export interface UpdateCheckAnnouncementInput {
  running: boolean;
  total: number;
  checked: number;
  outcome?: string | null | undefined;
  error?: string | null | undefined;
  updates: number;
  errors: number;
}

export const updateCheckAnnouncement = (
  wasRunning: boolean,
  next: UpdateCheckAnnouncementInput,
): string => {
  if (!wasRunning && next.running) {
    if (next.checked > 0) return `Update check running: ${next.checked} of ${countOf(next.total, "game", "games")} checked.`;
    return `Update check started for ${countOf(next.total, "game", "games")}.`;
  }
  if (!wasRunning || next.running) return "";
  const summary = updateCheckSummary({ ...next, outcome: next.outcome || "completed" });
  if (!summary) return "";
  if (summary.tone === "danger") return summary.reason ? `${summary.text} ${summary.reason.message}` : summary.text;
  if (summary.tone === "neutral") return summary.text;
  return `${summary.text} ${countOf(next.updates, "update", "updates")} found.`;
};

// The header's "Last check" value: a relative time ("3 days ago") with the
// full local date and time for a title. Unreadable values show as they are.
export interface LastCheckText {
  text: string;
  full: string;
}

export const lastCheckText = (value: string | null | undefined, nowMs = Date.now()): LastCheckText => {
  const raw = (value ?? "").trim();
  if (!raw) return { text: "never", full: "" };
  const date = new Date(raw);
  const time = date.getTime();
  if (!Number.isFinite(time)) return { text: raw, full: "" };
  return { text: formatRelativeAge(time / 1000, nowMs), full: date.toLocaleString() };
};

// The header's automatic check setting, in words.
export const autoCheckText = (frequency: string | null | undefined): string => {
  if (frequency === "off") return "Automatic check: off";
  if (frequency === "monthly") return "Automatic check: every month";
  return "Automatic check: every week";
};
