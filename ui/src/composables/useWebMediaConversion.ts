// SPDX-License-Identifier: GPL-3.0-or-later
// Video conversion jobs for the built-in player. The status poll keeps running
// after the dialog closes, and the end notification offers Play.
import { ref } from "vue";
import { api, type WebMediaStatus } from "../services/api";
import { notify } from "../utils/toast";
import { createWebMediaWatcher, summaryText } from "../utils/webMedia";

/** Latest status per game id, for the progress dialog. */
export const webMediaStatus = ref<Record<number, WebMediaStatus>>({});

interface Watched {
  title: string;
  play: () => void;
}
const watched = new Map<number, Watched>();

const watcher = createWebMediaWatcher({
  getStatus: (gameId) => api.getWebMediaConversionStatus(gameId),
  onUpdate: (gameId, status) => {
    webMediaStatus.value = { ...webMediaStatus.value, [gameId]: status };
  },
  onFinish: (gameId, status) => {
    const entry = watched.get(gameId);
    watched.delete(gameId);
    const { text, type } = summaryText(status);
    const play = entry && !status.error ? { label: "Play", run: entry.play } : undefined;
    notify(text, type, entry ? `${entry.title}: videos` : "Videos", play);
  },
});

export function useWebMediaConversion() {
  return {
    webMediaStatus,
    /** Follow a game's job (started here or found running at launch). */
    follow(gameId: number, title: string, play: () => void): void {
      watched.set(gameId, { title, play });
      watcher.watch(gameId);
    },
    async start(gameId: number, title: string, play: () => void): Promise<{ success: boolean; error?: string }> {
      const result = await api.startWebMediaConversion(gameId);
      if (!result.success) return { success: false, error: result.error || "Could not start the conversion." };
      watched.set(gameId, { title, play });
      watcher.watch(gameId);
      return { success: true };
    },
    async cancel(gameId: number): Promise<void> {
      const result = await api.cancelWebMediaConversion(gameId);
      if (result.success === false && result.error) notify(result.error, "error");
      watcher.watch(gameId);
    },
  };
}
