// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-preview mocks for the built-in player's video check and conversion
// (start_web_media_conversion, cancel_web_media_conversion,
// get_web_media_conversion_status, set_web_media_play_anyway).
//
// Demo: an HTML game whose title or path contains "h264" reports 12 videos the
// player cannot play. Set localStorage "wlib-mock-ffmpeg" to "missing" to see
// the install hint instead of "Convert videos".
import type { FfmpegInfo, GameRecord, LaunchGameResponse, WebMediaStatus } from "../api";

const MOCK_VIDEO_COUNT = 12;
// Slow enough for the 500 ms status poll to show progress in the browser preview.
const MOCK_FILE_MS = 600;
const BUSY = "This game's videos are being converted. Wait for the conversion to finish.";

const idle = (): WebMediaStatus => ({
  success: true,
  running: false,
  finished: false,
  done: 0,
  total: 0,
  current: "",
  converted: 0,
  failed: 0,
  failed_files: [],
  cancelled: false,
  error: "",
});

const mockFfmpeg = (): FfmpegInfo => {
  let missing = false;
  try {
    missing = localStorage.getItem("wlib-mock-ffmpeg") === "missing";
  } catch {
    missing = false;
  }
  return {
    available: !missing,
    ffmpeg: missing ? "" : "/usr/bin/ffmpeg",
    ffprobe: missing ? "" : "/usr/bin/ffprobe",
    video_encoder: missing ? "" : "libsvtav1",
    audio_encoder: missing ? "" : "libopus",
    reason: missing ? "missing" : "",
    install: { command: "sudo pacman -S ffmpeg", package_manager: "pacman", platform: "linux", host: false },
  };
};

export class MockWebMedia {
  /** Videos still to convert per game; absent means the game was not checked yet. */
  remaining = new Map<number, number>();
  playAnyway = new Set<number>();
  status = new Map<number, WebMediaStatus>();
  timer: ReturnType<typeof setInterval> | null = null;

  converting(gameId: number): boolean {
    return Boolean(this.status.get(gameId)?.running);
  }

  /** The launch result that stops the built-in player, or null. */
  launchCheck(game: GameRecord | undefined, target: string): LaunchGameResponse | null {
    if (!game) return null;
    if (this.converting(game.id)) return { success: false, code: "web_media_converting", error: BUSY };
    if (!/h264/i.test(`${game.title} ${target}`)) return null;
    if (!this.remaining.has(game.id)) this.remaining.set(game.id, MOCK_VIDEO_COUNT);
    const count = this.remaining.get(game.id) ?? 0;
    if (!count || this.playAnyway.has(game.id)) return null;
    return {
      success: false,
      code: "web_media_unsupported",
      error: `${count} videos use a format the built-in player cannot play.`,
      media: { count, ffmpeg: mockFfmpeg() },
    };
  }

  respond(method: string, args: unknown[], isRunning: (id: number) => boolean): unknown {
    const gameId = Number(args[0]);
    switch (method) {
      case "start_web_media_conversion": {
        if (!mockFfmpeg().available) return { success: false, code: "ffmpeg_unavailable", error: "Converting videos needs ffmpeg." };
        if ([...this.status.values()].some((entry) => entry.running)) return { success: false, error: "Another game's videos are being converted. Wait for it to finish." };
        if (isRunning(gameId)) return { success: false, error: "Close the game before converting its videos." };
        const total = this.remaining.get(gameId) ?? MOCK_VIDEO_COUNT;
        this.status.set(gameId, { ...idle(), running: true, total, current: "videos/clip-1.mp4" });
        this.tick();
        return { success: true, mock: true };
      }
      case "cancel_web_media_conversion": {
        const entry = this.status.get(gameId);
        if (!entry?.running) return { success: false, error: "No conversion is running for this game." };
        entry.cancelled = true;
        return { success: true, mock: true };
      }
      case "get_web_media_conversion_status":
        return { ...(this.status.get(gameId) ?? idle()), mock: true };
      case "set_web_media_play_anyway":
        if (!this.remaining.has(gameId)) return { success: false, error: "This game's videos were not checked yet." };
        this.playAnyway.add(gameId);
        return { success: true, mock: true };
      default:
        return undefined;
    }
  }

  tick(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      let active = false;
      for (const [gameId, entry] of this.status) {
        if (!entry.running) continue;
        if (entry.cancelled || entry.done >= entry.total) {
          entry.running = false;
          entry.finished = true;
          entry.current = "";
          continue;
        }
        entry.done += 1;
        // The demo fails one file to show the failed count.
        if (entry.done === 3) {
          entry.failed += 1;
          entry.failed_files = [...entry.failed_files, `videos/clip-${entry.done}.mp4`];
        } else {
          entry.converted += 1;
        }
        this.remaining.set(gameId, entry.total - entry.converted);
        entry.current = `videos/clip-${entry.done + 1}.mp4`;
        active = true;
      }
      if (!active && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    }, MOCK_FILE_MS);
  }
}
