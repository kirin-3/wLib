<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconFolder, IconFolderOpen, IconFolderX, IconStack2, IconTrash } from "@tabler/icons-vue";
import type { StorageGameRow } from "../../services/api";
import PathText from "../PathText.vue";
import {
  formatStorageBytes,
  gameRowFlags,
  gameRowParts,
  sortedStorageGames,
} from "../../utils/storage";

const props = defineProps<{
  games: Record<string, StorageGameRow>;
  busy?: boolean;
}>();

const emit = defineEmits<{
  (event: "open-folder", game: StorageGameRow): void;
  (event: "uninstall", game: StorageGameRow): void;
  (event: "pick-folder", game: StorageGameRow): void;
  (event: "clear-folder", game: StorageGameRow): void;
}>();

const orderedGames = computed(() => sortedStorageGames(props.games));
</script>

<template>
  <section class="storage-card rounded-xl p-6" aria-labelledby="storage-games-heading">
    <div class="flex flex-wrap items-baseline justify-between gap-2 mb-4">
      <h3 id="storage-games-heading" class="ui-section-heading text-lg font-semibold">
        <IconStack2 class="ui-section-icon" />
        <span>Largest games</span>
      </h3>
      <span class="text-xs" style="color: var(--text-muted)">
        {{ orderedGames.length }} {{ orderedGames.length === 1 ? "game" : "games" }}
      </span>
    </div>

    <p v-if="!orderedGames.length" class="text-sm" style="color: var(--text-muted)">
      No games with measured data were found in this report.
    </p>

    <ul v-else class="space-y-3" aria-label="Games by disk usage">
      <li
        v-for="row in orderedGames"
        :key="row.game_id"
        class="game-row rounded-xl p-4"
        :data-game-id="row.game_id"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1 space-y-1.5">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-sm truncate" style="color: var(--text-primary)">
                {{ row.title }}
              </span>
              <span
                v-for="flag in gameRowFlags(row)"
                :key="flag.kind"
                class="storage-flag text-xs font-medium px-2 py-0.5 rounded-full"
                :class="`storage-flag--${flag.tone}`"
                :data-flag="flag.kind"
              >
                {{ flag.label }}
              </span>
              <span
                v-if="row.picked"
                class="storage-flag storage-flag--info text-xs font-medium px-2 py-0.5 rounded-full"
              >
                picked folder
              </span>
            </div>

            <div v-if="row.root" class="text-xs font-mono" style="color: var(--text-muted)">
              <PathText :path="row.root" />
            </div>
            <p v-else-if="row.unsafe" class="text-xs" style="color: var(--danger-text)">
              {{ row.unsafe_reason || "Folder is not clear. Pick the game's folder to count it." }}
            </p>

            <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style="color: var(--text-secondary)">
              <span
                v-for="part in gameRowParts(row)"
                :key="part.key"
                class="tabular-nums"
              >
                {{ part.label }}: <strong style="color: var(--text-primary)">{{ formatStorageBytes(part.bytes) }}</strong>
              </span>
              <span v-if="row.torrent_bytes > 0" class="tabular-nums torrent-note">
                Torrent data (not in game total): <strong>{{ formatStorageBytes(row.torrent_bytes) }}</strong>
              </span>
            </div>
          </div>

          <div class="flex flex-col items-end gap-2 shrink-0">
            <span class="text-base font-bold tabular-nums" style="color: var(--text-primary)">
              {{ formatStorageBytes(row.total_bytes) }}
            </span>

            <div class="flex flex-wrap items-center justify-end gap-2">
              <button
                type="button"
                class="storage-btn px-2.5 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                :disabled="!row.root && !row.exe_path"
                :aria-label="`Open folder for ${row.title}`"
                @click="emit('open-folder', row)"
              >
                <IconFolderOpen class="w-3.5 h-3.5" />
                <span>Open folder</span>
              </button>

              <button
                type="button"
                class="storage-btn px-2.5 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                :disabled="busy || !row.exe_path"
                :aria-label="`Pick folder for ${row.title}`"
                @click="emit('pick-folder', row)"
              >
                <IconFolder class="w-3.5 h-3.5" />
                <span>Pick folder</span>
              </button>

              <button
                v-if="row.picked"
                type="button"
                class="storage-btn px-2.5 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                :disabled="busy"
                :aria-label="`Clear picked folder for ${row.title}`"
                @click="emit('clear-folder', row)"
              >
                <IconFolderX class="w-3.5 h-3.5" />
                <span>Clear</span>
              </button>

              <button
                type="button"
                class="storage-btn storage-btn--danger px-2.5 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                :disabled="!row.exe_path"
                :aria-label="`Uninstall ${row.title}`"
                @click="emit('uninstall', row)"
              >
                <IconTrash class="w-3.5 h-3.5" />
                <span>Uninstall</span>
              </button>
            </div>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.game-row {
  background: var(--bg-inset);
  border: 1px solid var(--border);
}
.storage-flag--warn {
  background: rgba(245, 158, 11, 0.16);
  color: #f59e0b;
  border: 1px solid rgba(245, 158, 11, 0.35);
}
.storage-flag--danger {
  background: var(--danger-bg);
  color: var(--danger-text);
  border: 1px solid var(--danger-border);
}
.storage-flag--info {
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.torrent-note {
  color: var(--text-muted);
}
.storage-btn {
  color: var(--text-primary);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.storage-btn:hover:not(:disabled) {
  background: var(--bg-overlay);
}
.storage-btn--danger {
  color: var(--danger-text);
  border-color: var(--danger-border);
}
.storage-btn--danger:hover:not(:disabled) {
  background: var(--danger-bg);
}
.storage-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.storage-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
