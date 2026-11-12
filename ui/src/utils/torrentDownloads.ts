// SPDX-License-Identifier: GPL-3.0-or-later
import type { RpdlSearchResponse, RpdlTorrent, UpdateDownloadJob } from "../services/api";

/** Torrent jobs that still count as "a download is running" for a game. */
export const TORRENT_RUNNING_STAGES = ["fetching", "downloading", "paused"];

export const isTorrentJob = (job: UpdateDownloadJob): boolean => job.kind === "torrent";

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"];

export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${Math.round(value)} B` : `${value.toFixed(value >= 100 ? 0 : 1)} ${UNITS[unit]}`;
}

export const formatSpeed = (bytesPerSecond: number): string => `${formatSize(bytesPerSecond)}/s`;

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return "under a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** YYYY-MM-DD from a Unix time; empty when unknown. */
export function formatUploadDate(unix: number): string {
  if (!unix || unix <= 0) return "";
  return new Date(unix * 1000).toISOString().slice(0, 10);
}

export function torrentPercent(job: UpdateDownloadJob): number | null {
  if (!job.total) return null;
  return Math.min(100, Math.max(0, Math.floor((job.bytes / job.total) * 100)));
}

/** What a torrent job is doing, in one line. */
export function torrentStatusLine(job: UpdateDownloadJob): string {
  if (job.stage === "failed") return job.error || "Torrent download failed";
  if (job.stage === "fetching") return "Fetching the torrent…";
  if (job.stage === "paused") return job.error || "Paused";
  if (job.hint) return job.hint;
  const percent = torrentPercent(job);
  return percent === null ? "Downloading" : `Downloading ${percent}%`;
}

/** Speeds, peers and time left, for a downloading torrent. */
export function torrentDetails(job: UpdateDownloadJob): string {
  if (job.stage !== "downloading") return "";
  const parts = [`↓ ${formatSpeed(job.speed)}`, `↑ ${formatSpeed(job.upload_speed ?? 0)}`];
  const peers = job.peers ?? 0;
  parts.push(`${peers} ${peers === 1 ? "peer" : "peers"}`);
  const eta = formatEta(job.eta);
  if (eta) parts.push(`${eta} left`);
  return parts.join(" · ");
}

export const isSeeding = (job: UpdateDownloadJob): boolean =>
  isTorrentJob(job) && job.stage === "done" && job.seed_state === "seeding";

export const needsSeedAnswer = (job: UpdateDownloadJob): boolean =>
  isTorrentJob(job) && job.stage === "done" && job.seed_state === "awaiting_answer";

/** "Seeding · ratio 0.4 of 1.0 · ↑ 120 KiB/s" for the Updates view. */
export function seedingLabel(job: UpdateDownloadJob): string {
  const ratio = (job.ratio ?? 0).toFixed(1);
  const target = job.ratio_target ? ` of ${job.ratio_target.toFixed(1)}` : "";
  return `Seeding · ratio ${ratio}${target} · ↑ ${formatSpeed(job.upload_speed ?? 0)}`;
}

export interface SeedPromptText {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  checkboxLabel: string;
}

/** The seeding question; a game folder costs a second copy until seeding ends. */
export function seedPromptText(job: UpdateDownloadJob): SeedPromptText {
  const target = job.ratio_target ? job.ratio_target.toFixed(1) : "";
  const goal = target ? ` until ratio ${target}` : "";
  const folderNote = job.payload_kind === "folder"
    ? `\n\nSeeding keeps a second ${formatSize(job.total ?? 0)} copy of the game until seeding ends.`
    : "";
  return {
    title: "Keep seeding?",
    message: `Keep seeding ${job.title || "this game"}${goal}?${folderNote}`,
    confirmLabel: "Seed",
    cancelLabel: "Don't seed",
    checkboxLabel: "Don't ask again",
  };
}

/** What the install dialog should open with for a finished torrent. */
export function torrentInstallSource(job: UpdateDownloadJob): { archivePath: string; sourceKind: "archive" | "folder" } | null {
  if (job.archive_path) return { archivePath: job.archive_path, sourceKind: "archive" };
  if (job.folder_path) return { archivePath: job.folder_path, sourceKind: "folder" };
  return null;
}

/** Marks for one RPDL result row. */
export function rpdlMarks(torrent: RpdlTorrent): Array<{ text: string; tone: "warn" | "good" }> {
  const marks: Array<{ text: string; tone: "warn" | "good" }> = [];
  if (torrent.version_match === true) marks.push({ text: "Matches version", tone: "good" });
  if (torrent.version_match === false) marks.push({ text: "Different version", tone: "warn" });
  if (torrent.no_seeders) marks.push({ text: "No seeders", tone: "warn" });
  return marks;
}

/** Whether a clear RPDL match replaces the file-host preselection (needs a signed-in account). */
export function rpdlPreselected(response: RpdlSearchResponse | null): RpdlTorrent | null {
  if (!response?.success || !response.signed_in || response.preselected_id == null) return null;
  return response.results?.find((torrent) => torrent.id === response.preselected_id) ?? null;
}

/** The note under the RPDL list. */
export function rpdlNote(response: RpdlSearchResponse | null): string {
  if (!response?.success || !response.results?.length) return "";
  if (!response.signed_in) return "Sign in to RPDL in Settings to download these.";
  if (response.needs_choice) return "Several torrents match closely. Choose one.";
  return "";
}

export type RpdlPrimaryAction = "download" | "send";

/** "send" hands the torrent to the external client's watch folder; "download" uses the built-in client. */
export function rpdlPrimaryAction(watchFolder: string | null | undefined): RpdlPrimaryAction {
  return (watchFolder ?? "").trim() ? "send" : "download";
}

export const rpdlPrimaryLabel = (action: RpdlPrimaryAction): string => action === "send" ? "Send to client" : "Download";

/** The callout text after Send to client wrote the `.torrent` file. */
export function torrentSentMessage(folder: string): string {
  const where = folder.trim() ? `the watch folder (${folder.trim()})` : "the watch folder";
  return `Sent to ${where}. Your torrent client will download it. When it finishes, install the update with "Install from archive".`;
}
