// SPDX-License-Identifier: GPL-3.0-or-later
// The built-in player's video dialog: state, texts, and the conversion status poll.
import type { FfmpegInfo, WebMediaStatus } from "../services/api.ts";
import type { ToastType } from "./toast.ts";

export const WEB_MEDIA_UNSUPPORTED = "web_media_unsupported";
export const WEB_MEDIA_CONVERTING = "web_media_converting";
export const WEB_MEDIA_POLL_MS = 500;

/** "choices": the three actions (with the ffmpeg hint when it cannot convert); "confirm": the "cannot be undone" step. */
export type WebMediaStep = "choices" | "confirm" | "progress";

export const initialStep = (code: string): WebMediaStep => (code === WEB_MEDIA_CONVERTING ? "progress" : "choices");

export const canConvert = (info: FfmpegInfo | null | undefined): boolean => Boolean(info?.available);

/** Why "Convert videos" is not available, or "". */
export const ffmpegProblem = (info: FfmpegInfo | null | undefined): string => {
  if (!info || info.available) return "";
  if (info.reason === "no_video_encoder") return "The installed ffmpeg cannot encode AV1 or VP9 video.";
  if (info.reason === "no_audio_encoder") return "The installed ffmpeg cannot encode Opus audio.";
  return "Converting videos needs ffmpeg, which wLib cannot find.";
};

/** How to install ffmpeg here: a short note and the command to copy ("" when unknown). */
export const ffmpegInstallHint = (info: FfmpegInfo | null | undefined): { note: string; command: string } => {
  const install = info?.install;
  if (!install) return { note: "Install ffmpeg, then try again.", command: "" };
  if (install.platform === "windows") return { note: "Install ffmpeg with this command, then restart wLib:", command: install.command };
  if (install.package_manager === "nixos") return { note: "Add ffmpeg to your NixOS configuration:", command: install.command };
  const where = install.host ? "on your system (outside the Flatpak)" : "";
  if (!install.command) return { note: `Install ffmpeg ${where} with your package manager, then try again.`.replace(/\s+/g, " "), command: "" };
  return { note: `Install ffmpeg ${where} with this command:`.replace(/\s+/g, " "), command: install.command };
};

export const unsupportedText = (count: number): string =>
  `${count} ${count === 1 ? "video uses" : "videos use"} a format the built-in player cannot play (H.264, H.265 or AAC).`;

export const progressText = (status: WebMediaStatus | null | undefined): string => {
  if (!status || !status.total) return "Checking the videos...";
  return `${status.done} of ${status.total} videos`;
};

export const progressPercent = (status: WebMediaStatus | null | undefined): number =>
  status && status.total ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;

/** The end notification for a finished job. */
export const summaryText = (status: WebMediaStatus): { text: string; type: ToastType } => {
  const converted = `${status.converted} ${status.converted === 1 ? "video" : "videos"} converted`;
  const failed = status.failed ? `, ${status.failed} could not be converted` : "";
  if (status.error) return { text: `${status.error} ${converted}${failed}.`, type: "error" };
  if (status.cancelled) return { text: `Conversion stopped. ${converted}${failed}.`, type: "info" };
  return { text: `${converted}${failed}.`, type: status.failed ? "error" : "success" };
};

export interface WebMediaWatcherDeps {
  getStatus: (gameId: number) => Promise<WebMediaStatus>;
  onUpdate: (gameId: number, status: WebMediaStatus) => void;
  onFinish: (gameId: number, status: WebMediaStatus) => void;
  intervalMs?: number;
}

/** Polls each watched game's job until it ends, also when no dialog is open. */
export function createWebMediaWatcher(deps: WebMediaWatcherDeps) {
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  const interval = deps.intervalMs ?? WEB_MEDIA_POLL_MS;

  const poll = async (gameId: number): Promise<void> => {
    timers.delete(gameId);
    let status: WebMediaStatus | null = null;
    try {
      status = await deps.getStatus(gameId);
    } catch (error) {
      console.error("Video conversion status failed", error);
    }
    if (status && status.success !== false) {
      deps.onUpdate(gameId, status);
      if (!status.running) {
        if (status.finished) deps.onFinish(gameId, status);
        return;
      }
    }
    if (!stopped.has(gameId)) timers.set(gameId, setTimeout(() => void poll(gameId), interval));
  };

  const stopped = new Set<number>();
  return {
    /** Start polling a game now (no-op while it is already polled). */
    watch(gameId: number): void {
      stopped.delete(gameId);
      const pending = timers.get(gameId);
      if (pending !== undefined) clearTimeout(pending);
      void poll(gameId);
    },
    stop(gameId: number): void {
      stopped.add(gameId);
      const pending = timers.get(gameId);
      if (pending !== undefined) clearTimeout(pending);
      timers.delete(gameId);
    },
    watching: (gameId: number): boolean => timers.has(gameId),
  };
}
