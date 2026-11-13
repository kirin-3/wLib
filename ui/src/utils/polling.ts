// SPDX-License-Identifier: GPL-3.0-or-later

// Each status poll is a bridge round trip that wakes Python, Qt and the
// renderer, so polls slow down while nobody looks at the window. A slow poll,
// not a stopped one, keeps the app working if focus reporting is wrong.
export const INACTIVE_POLL_MS = 30_000;

export function windowActive(): boolean {
  if (typeof document === "undefined") return true;
  return document.visibilityState === "visible" && document.hasFocus();
}

export function pollDelay(baseMs: number, active: boolean): number {
  return active ? baseMs : Math.max(baseMs, INACTIVE_POLL_MS);
}

export interface Poller {
  /** Polls now, unless a poll is already running, and restarts the wait. */
  runNow(): void;
  stop(): void;
}

/** Runs `task` repeatedly, waiting `baseMs()` between runs (longer while the window is inactive). */
export function startPoller(
  task: () => unknown,
  baseMs: () => number,
  isActive: () => boolean = windowActive,
): Poller {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running = false;
  let stopped = false;
  const schedule = () => {
    if (stopped) return;
    timer = setTimeout(run, pollDelay(baseMs(), isActive()));
  };
  const run = async () => {
    timer = undefined;
    if (running || stopped) return;
    running = true;
    try {
      await task();
    } catch (error) {
      console.error("Poll failed", error);
    } finally {
      running = false;
      schedule();
    }
  };
  schedule();
  return {
    runNow() {
      if (stopped || running) return;
      if (timer) clearTimeout(timer);
      void run();
    },
    stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = undefined;
    },
  };
}
