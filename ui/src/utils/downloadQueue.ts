// SPDX-License-Identifier: GPL-3.0-or-later
// Pure helpers for the Downloads page, the sidebar entry and the download
// status on a game. The backend reports states and failure codes; the words
// and the actions live here.
import type {
  DownloadFailureCode,
  DownloadForecast,
  DownloadOffer,
  DownloadQueueJob,
  DownloadQueueSummary,
  UpdateDownloadJob,
} from "../services/api";
import { formatEta, formatSize, formatSpeed } from "./torrentDownloads.ts";

export type SectionId = "needs" | "downloading" | "upnext" | "paused" | "ready" | "seeding";

export const SECTION_ORDER: SectionId[] = ["needs", "downloading", "upnext", "paused", "ready", "seeding"];

export const SECTION_TITLES: Record<SectionId, string> = {
  needs: "Needs you",
  downloading: "Downloading",
  upnext: "Up next",
  paused: "Paused",
  ready: "Ready to install",
  seeding: "Seeding",
};

/** A finished torrent whose seeding question is still open. */
export const awaitsSeedAnswer = (job: DownloadQueueJob): boolean =>
  job.state === "ready" && job.kind === "torrent" && job.details.seed_state === "awaiting_answer";

export const isUnfinished = (job: DownloadQueueJob): boolean => job.state !== "ready";

export function sectionOf(job: DownloadQueueJob): Exclude<SectionId, "seeding"> {
  switch (job.state) {
    case "attention":
      return "needs";
    case "running":
      return "downloading";
    case "preparing":
    case "queued":
    case "retry_wait":
    case "held":
      return "upnext";
    case "paused":
      return "paused";
    case "ready":
      return awaitsSeedAnswer(job) ? "needs" : "ready";
  }
}

export interface DownloadSection {
  id: SectionId;
  title: string;
  jobs: DownloadQueueJob[];
  seeding: UpdateDownloadJob[];
  offers: DownloadOffer[];
  count: number;
}

/** The sections that hold something, in page order. */
export function groupDownloads(
  jobs: DownloadQueueJob[],
  seeding: UpdateDownloadJob[] = [],
  offers: DownloadOffer[] = [],
): DownloadSection[] {
  const sections: DownloadSection[] = [];
  for (const id of SECTION_ORDER) {
    const sectionJobs = id === "seeding" ? [] : jobs.filter((job) => sectionOf(job) === id);
    const sectionSeeding = id === "seeding" ? seeding : [];
    const sectionOffers = id === "ready" ? offers : [];
    const count = sectionJobs.length + sectionSeeding.length + sectionOffers.length;
    if (count) sections.push({ id, title: SECTION_TITLES[id], jobs: sectionJobs, seeding: sectionSeeding, offers: sectionOffers, count });
  }
  return sections;
}

export function sectionHeading(section: DownloadSection, queue?: Pick<DownloadQueueSummary, "active" | "max_active">): string {
  if (section.id === "downloading" && queue) return `${section.title} · ${section.count} · ${queue.active} of ${queue.max_active} slots in use`;
  return `${section.title} · ${section.count}`;
}

const HOST_NAMES: Record<string, string> = { pixeldrain: "Pixeldrain", mega: "MEGA", buzzheavier: "Buzzheavier" };

/** "RPDL torrent", or the file host's name. */
export function sourceLabel(job: Pick<DownloadQueueJob, "kind" | "source">): string {
  if (job.kind === "torrent") return "RPDL torrent";
  const host = (job.source.host ?? "").toLowerCase();
  return HOST_NAMES[host] ?? (job.source.host || "File host");
}

export function percentOf(job: Pick<DownloadQueueJob, "bytes_done" | "bytes_total">): number | null {
  if (!job.bytes_total || job.bytes_total <= 0) return null;
  return Math.min(100, Math.max(0, Math.floor((job.bytes_done / job.bytes_total) * 100)));
}

export function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

const HOLD_WORDS = { game: "while a game runs", battery: "while on battery" } as const;

/** The state of a download, in words. */
export function stateText(job: DownloadQueueJob): string {
  const stage = job.details.stage ?? "";
  switch (job.state) {
    case "preparing":
      if (job.kind === "torrent") return "Fetching the torrent";
      return "Getting the download link";
    case "queued":
      return job.bytes_total ? `Waiting · ${formatSize(job.bytes_total)}` : "Waiting";
    case "retry_wait":
      return job.retry_in !== null && job.retry_in > 0 ? `Retrying in ${formatSeconds(job.retry_in)}` : "Retrying soon";
    case "held":
      return `Waiting ${job.reason === "battery" ? HOLD_WORDS.battery : HOLD_WORDS.game}`;
    case "running": {
      if (job.details.restarting) return "Torrent engine stopped, restarting";
      if (stage === "resolving") return "Getting the download link";
      if (stage === "verifying") return job.kind === "host" && job.bytes_done === 0 ? "Checking the file" : "Verifying archive";
      if (job.details.hint) return job.details.hint;
      const percent = percentOf(job);
      return percent === null ? "Downloading" : `Downloading ${percent}%`;
    }
    case "paused": {
      const percent = percentOf(job);
      if (job.reason === "preparing") return "Paused";
      return percent === null || percent === 0 ? "Paused" : `Paused at ${percent}%`;
    }
    case "attention":
      if (job.reason === "browser_check") return "Finish the check in the browser window that opened";
      return "Needs you";
    case "ready":
      if (awaitsSeedAnswer(job)) return "Finished · Keep seeding?";
      return "Ready to install";
  }
}

/** Bytes, percentage, speed, time left and peers, as shown on a row. */
export function progressText(job: DownloadQueueJob): string {
  const parts: string[] = [];
  if (job.bytes_total) {
    parts.push(`${formatSize(job.bytes_done)} of ${formatSize(job.bytes_total)}`);
    const percent = percentOf(job);
    if (percent !== null) parts.push(`${percent}%`);
  } else if (job.bytes_done) {
    parts.push(formatSize(job.bytes_done));
  }
  if (job.state === "running") {
    parts.push(formatSpeed(job.speed));
    if (job.kind === "torrent") parts.push(`↑ ${formatSpeed(job.details.upload_speed ?? 0)}`);
    const eta = job.bytes_total ? formatEta(job.eta) : "";
    if (eta) parts.push(`${eta} left`);
    if (job.kind === "torrent") {
      const peers = job.details.peers ?? 0;
      parts.push(`${peers} ${peers === 1 ? "peer" : "peers"}`);
    }
  }
  return parts.join(" · ");
}

export type FailureAction = "retry" | "another" | "settings" | "remove";

export interface FailureInfo {
  sentence: string;
  actions: FailureAction[];
}

/** "It stopped at 43%." when data had arrived. */
function stoppedAt(job: DownloadQueueJob): string {
  const percent = percentOf(job);
  if (percent !== null && percent > 0) return ` It stopped at ${percent}%.`;
  if (job.bytes_done > 0) return ` It stopped after ${formatSize(job.bytes_done)}.`;
  return "";
}

/** The cause in plain words and the actions that fit it. */
export function failureInfo(job: DownloadQueueJob, freeBytes: number | null = null): FailureInfo {
  const torrent = job.kind === "torrent";
  const another: FailureAction = "another";
  const code: DownloadFailureCode | "" = job.error_code;
  const at = stoppedAt(job);
  const text = job.error_text.toLowerCase();
  if (job.reason === "browser_check" && !code) {
    return { sentence: "Finish the check in the browser window that opened. The download continues after it.", actions: ["remove"] };
  }
  switch (code) {
    case "network":
      return {
        sentence: torrent ? `The connection to RPDL failed.${at}` : `The host stopped responding.${at}`,
        actions: ["retry", another, "remove"],
      };
    case "removed":
      return {
        sentence: torrent ? "RPDL no longer has this torrent." : "The file was removed from the host, or its link expired.",
        actions: [another, "remove"],
      };
    case "quota":
      return {
        sentence: torrent
          ? "RPDL's rate limit was reached. Wait a moment, then retry."
          : `The host's transfer quota or rate limit was reached. Wait for it to reset, or choose another mirror.${at}`,
        actions: ["retry", another, "remove"],
      };
    case "challenge":
      return { sentence: "The host asked for a check in the browser. Choose another mirror, or download this one in the browser.", actions: [another, "retry", "remove"] };
    case "invalid":
      return {
        sentence: torrent ? "RPDL sent a torrent that is not valid." : "The file is not a valid archive, or wLib cannot download this mirror. Choose another mirror.",
        actions: [another, "remove"],
      };
    case "disk": {
      const needed = job.bytes_total ? Math.max(0, job.bytes_total - job.bytes_done) : null;
      const amounts = needed !== null && freeBytes !== null ? ` It needs ${formatSize(needed)} and ${formatSize(freeBytes)} is free.` : "";
      return { sentence: `Not enough disk space on the downloads drive.${amounts} Free some space, then retry.`, actions: ["retry", "remove"] };
    }
    case "write":
      return { sentence: "wLib could not write the download to its data folder. Check the folder and the drive, then retry.", actions: ["retry", "remove"] };
    case "sign_in":
      if (torrent || text.includes("rpdl")) return { sentence: "Sign in to RPDL in Settings, then retry.", actions: ["settings", "retry", another, "remove"] };
      if (text.includes("f95zone")) return { sentence: "Sign in to F95Zone in your browser, then retry.", actions: ["retry", another, "remove"] };
      return { sentence: "The file-host account needs a new sign-in. Update it in Settings, then retry.", actions: ["settings", "retry", another, "remove"] };
    case "extension":
      return { sentence: "Open a browser with the wLib extension, then retry.", actions: ["retry", another, "remove"] };
    case "check_timeout":
      return { sentence: "The check in the browser was not finished. Finish it in the browser window, then retry, or choose another mirror.", actions: ["retry", another, "remove"] };
    case "permission":
      return { sentence: "Allow Buzzheavier downloads in the wLib extension settings, then retry.", actions: ["retry", another, "remove"] };
    case "mirror_gone":
      return { sentence: "The thread no longer offers this mirror. Choose another one.", actions: [another, "remove"] };
    case "engine":
      return { sentence: "The torrent engine stopped. Retry, or choose a file-host mirror.", actions: ["retry", another, "remove"] };
    default:
      return { sentence: `${job.error_text || "The download failed"}.${at}`.replace(/\.\.$/, "."), actions: ["retry", another, "remove"] };
  }
}

export const FAILURE_ACTION_LABELS: Record<FailureAction, string> = {
  retry: "Retry",
  another: "Choose another",
  settings: "Open Settings",
  remove: "Remove",
};

export type RowAction = "pause" | "resume" | "now" | "up" | "down" | "top" | "remove" | "install" | FailureAction;

/** The actions a download offers in its state. */
export function rowActions(job: DownloadQueueJob): RowAction[] {
  switch (job.state) {
    case "running":
      return ["pause", "remove"];
    case "preparing":
      return ["pause", "up", "down", "top", "remove"];
    case "queued":
    case "retry_wait":
    case "held":
      return ["now", "pause", "up", "down", "top", "remove"];
    case "paused":
      return ["resume", "now", "remove"];
    case "attention":
      return failureInfo(job).actions;
    case "ready":
      return awaitsSeedAnswer(job) ? [] : ["install", "remove"];
  }
}

/** A row that holds downloaded data asks before Remove deletes it. */
export const removeNeedsConfirm = (job: DownloadQueueJob): boolean => job.bytes_done > 0 || job.state === "ready";

/** Pausing a download that cannot continue throws its data away. */
export const pauseNeedsConfirm = (job: DownloadQueueJob): boolean =>
  !job.can_continue && (job.state === "running" || job.bytes_done > 0);

/** Time left for every running and waiting download, when known. */
export function totalTimeLeft(jobs: DownloadQueueJob[], speed: number): number | null {
  if (speed <= 0) return null;
  let remaining = 0;
  for (const job of jobs) {
    if (!["running", "queued", "preparing", "retry_wait", "held"].includes(job.state)) continue;
    if (!job.bytes_total) return null;
    remaining += Math.max(0, job.bytes_total - job.bytes_done);
  }
  return remaining / speed;
}

export function forecastText(forecast: DownloadForecast): { text: string; warning: boolean } {
  const unknown = forecast.unknown
    ? ` ${forecast.unknown === 1 ? "1 download has" : `${forecast.unknown} downloads have`} an unknown size.`
    : "";
  if (forecast.free === null) return { text: `The downloads need ${formatSize(forecast.needed)}.${unknown}`, warning: false };
  if (!forecast.fits) {
    return { text: `Not enough space: the downloads need ${formatSize(forecast.missing)} more than the ${formatSize(forecast.free)} that is free.${unknown}`, warning: true };
  }
  return { text: `The downloads need ${formatSize(forecast.needed)}; ${formatSize(forecast.free)} is free.${unknown}`, warning: false };
}

export function holdText(hold: DownloadQueueSummary["hold"]): string {
  if (hold === "game") return "Downloads wait while a game runs. They continue when the game closes.";
  if (hold === "battery") return "Downloads wait while the computer runs on battery. They continue on mains power.";
  return "";
}

/** The sidebar entry: unfinished downloads and whether one needs the user. */
export function sidebarBadge(jobs: DownloadQueueJob[]): { count: number; warning: boolean } {
  return {
    count: jobs.filter(isUnfinished).length,
    warning: jobs.some((job) => job.state === "attention" || awaitsSeedAnswer(job)),
  };
}

/** The short status on a game in the Updates view and the game details. */
export function gameDownloadStatus(job: DownloadQueueJob | undefined): string {
  if (!job) return "";
  switch (job.state) {
    case "running": {
      const percent = percentOf(job);
      return percent === null ? "Downloading" : `Downloading ${percent}%`;
    }
    case "preparing":
    case "queued":
    case "retry_wait":
    case "held":
      return "Queued";
    case "paused":
      return "Paused";
    case "attention":
      return "Download needs you";
    case "ready":
      return awaitsSeedAnswer(job) ? "Download needs you" : "Ready to install";
  }
}

/** The job a game's status follows: an unfinished one first, else a finished one. */
export function jobForGame(jobs: DownloadQueueJob[], gameId: number): DownloadQueueJob | undefined {
  return jobs.find((job) => job.game_id === gameId && isUnfinished(job)) ?? jobs.find((job) => job.game_id === gameId);
}

/**
 * Open the install dialog for a download that just finished only when no
 * other download is unfinished, the game does not run, and no install dialog
 * is open. Otherwise the page and a notification offer it.
 */
export function shouldAutoOpen(
  job: DownloadQueueJob,
  jobs: DownloadQueueJob[],
  runningGames: number[],
  installDialogOpen: boolean,
): boolean {
  if (job.state !== "ready" || installDialogOpen || runningGames.includes(job.game_id)) return false;
  return !jobs.some((other) => other.job_id !== job.job_id && isUnfinished(other));
}

/** The archive or folder a finished download installs from. */
export function installSource(job: DownloadQueueJob): { archivePath: string; sourceKind: "archive" | "folder" } | null {
  if (job.details.archive_path) return { archivePath: job.details.archive_path, sourceKind: "archive" };
  if (job.details.folder_path) return { archivePath: job.details.folder_path, sourceKind: "folder" };
  return null;
}

/** Polyline points for the speed chart; the highest sample touches the top. */
export function chartPoints(history: number[], width: number, height: number): string {
  if (history.length < 2) return "";
  const peak = Math.max(...history, 1);
  const step = width / (history.length - 1);
  return history
    .map((value, index) => `${(index * step).toFixed(1)},${(height - (value / peak) * height).toFixed(1)}`)
    .join(" ");
}

/** The chart's text alternative. */
export function chartSummary(history: number[]): string {
  if (!history.length) return "No speed samples yet.";
  const peak = Math.max(...history);
  const average = history.reduce((sum, value) => sum + value, 0) / history.length;
  const minutes = Math.max(1, Math.round(history.length / 60));
  return `Combined download speed over the last ${minutes} ${minutes === 1 ? "minute" : "minutes"}: average ${formatSpeed(average)}, peak ${formatSpeed(peak)}.`;
}

/** The message for an assistive-technology live region when a download changes state. */
export function stateChangeAnnouncement(previous: DownloadQueueJob | undefined, job: DownloadQueueJob): string {
  if (!previous || previous.state === job.state) return "";
  const name = job.title || "A download";
  switch (job.state) {
    case "running": return `${name} is downloading.`;
    case "paused": return `${name} is paused.`;
    case "attention": return `${name} needs you.`;
    case "ready": return `${name} is ready to install.`;
    case "held": return `${name} waits ${job.reason === "battery" ? HOLD_WORDS.battery : HOLD_WORDS.game}.`;
    case "retry_wait": return `${name} retries soon.`;
    default: return `${name} is waiting.`;
  }
}

/** Downloads that move bytes or wait for a step that ends soon. */
const pollsFast = (job: DownloadQueueJob): boolean =>
  job.state === "running" || job.state === "preparing" || (job.state === "attention" && job.reason === "browser_check");

/**
 * Poll quickly while bytes move, often while a download is unfinished, and
 * rarely when none is: an idle poll costs CPU for nothing.
 */
export const downloadPollMs = (jobs: DownloadQueueJob[]): number =>
  jobs.some(pollsFast) ? 500 : jobs.some(isUnfinished) ? 2000 : 10_000;

/** Runs one queue command, reports a refusal, and refreshes the list either way. */
export async function runQueueCommand(
  command: () => Promise<{ success?: boolean | undefined; error?: string | undefined }>,
  refresh: () => unknown,
  report: (message: string) => void,
): Promise<boolean> {
  let ok = false;
  try {
    const result = await command();
    ok = result.success === true;
    if (!ok) report(result.error || "The download could not be changed.");
  } catch (error) {
    report(error instanceof Error ? error.message : "The download could not be changed.");
  }
  await refresh();
  return ok;
}

/** The slot choices for "Downloads at the same time" (1 to 5). */
export const MAX_ACTIVE_DOWNLOADS = [1, 2, 3, 4, 5] as const;
/** Same top value as MAX_LIMIT_KIB in core/downloads/queue_settings.py. */
export const MAX_LIMIT_KIB = 10_485_760;

/** A speed limit the backend accepts: a whole number of KiB/s, 0 for none. */
export function clampLimitKib(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  return Math.min(MAX_LIMIT_KIB, Math.round(number));
}

/**
 * The queue order after moving one waiting download: `waiting` holds the
 * "Up next" rows in page order, `all` every job id in queue order.
 */
export function moveOrder(all: string[], waiting: string[], id: string, to: number): string[] {
  const from = waiting.indexOf(id);
  if (from < 0) return all;
  const target = Math.max(0, Math.min(waiting.length - 1, to));
  const moved = waiting.filter((item) => item !== id);
  moved.splice(target, 0, id);
  // The waiting rows keep their slots in the full order; only their order changes.
  const slots = new Set(waiting);
  let next = 0;
  return all.map((item) => (slots.has(item) ? moved[next++] ?? item : item));
}

/** The notification after the picker adds a download. */
export function addedNotice(title: string, waitsForGame: boolean): string {
  const name = title || "The update";
  return waitsForGame
    ? `${name} was added to Downloads. It starts when the game closes.`
    : `${name} was added to Downloads.`;
}
