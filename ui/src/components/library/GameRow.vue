<script setup lang="ts">
// SPDX-License-Identifier: GPL-3.0-or-later
// One list row. Props are primitives and handlers are emitted with the game, so
// the parent can bind method references and Vue skips this row when only
// another game changed.
import { computed } from "vue";
import {
  IconArrowBigUpFilled,
  IconCheck,
  IconHeartFilled,
  IconLoader2,
  IconDownload,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconPointFilled,
} from "@tabler/icons-vue";
import CoverImage from "../CoverImage.vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import type { GameRecord } from "../../services/api";
import { gameCoverSrc } from "../../utils/gameCover";
import {
  formatLastPlayed,
  formatPlaytime,
  getEffectiveLaunchTargets,
  getGamePlayStatusMeta,
  hasAdditionalLaunchTargets,
  hasAvailableUpdate,
  hasUnplayedUpdate,
  isWishlisted,
} from "../../utils/libraryGames";

const props = defineProps<{
  game: GameRecord;
  selected: boolean;
  running: boolean;
  /** "moving" or "waiting" while the game is in the move queue. */
  moveState?: "" | "moving" | "waiting";
  movePercent?: number;
  primary: boolean;
  /** True while the library is in selection mode; only then the row exposes its selected state. */
  selectionMode: boolean;
  /** Position in the table for assistive technology (the header row is 1). */
  rowIndex: number;
}>();
const emit = defineEmits<{
  activate: [game: GameRecord, event: MouseEvent | KeyboardEvent];
  contextmenu: [game: GameRecord, event: MouseEvent | KeyboardEvent];
  "toggle-game": [game: GameRecord];
  "target-select": [game: GameRecord, event: Event];
}>();

const wishlisted = computed(() => isWishlisted(props.game));
const updateAvailable = computed(() => hasAvailableUpdate(props.game));
const unplayed = computed(() => hasUnplayedUpdate(props.game));
const playStatus = computed(() => getGamePlayStatusMeta(props.game));
// An unplayed game shows a dash in both columns, like the cards that leave playtime out.
const playtimeText = computed(() => (props.game.playtime_seconds ? formatPlaytime(props.game.playtime_seconds) : ""));
const lastPlayedText = computed(() => (props.game.last_played ? formatLastPlayed(props.game.last_played) : ""));
const quietPlay = computed(() => !props.running && !wishlisted.value);
const additionalTargets = computed(() => hasAdditionalLaunchTargets(props.game, props.running));
const effectiveTargets = computed(() => (additionalTargets.value ? getEffectiveLaunchTargets(props.game) : []));
const playLabel = computed(() => `${wishlisted.value ? "Set up" : props.running ? "Stop" : "Play"} ${props.game.title}`);
</script>

<template>
    <tr
      :data-game-id="game.id"
      :data-controller-primary="primary ? '' : undefined"
      tabindex="0"
      role="row"
      :aria-rowindex="rowIndex"
      :aria-label="game.title"
      aria-describedby="library-item-hint"
      @keydown.enter.self.prevent="emit('activate', game, $event)"
      @keydown.space.self.prevent="emit('activate', game, $event)"
      class="game-row"
      :class="{ 'is-running': running, 'is-selected': selected }"
      :aria-selected="selectionMode ? selected : undefined"
      @mousedown.shift.prevent
      @click="emit('activate', game, $event)"
      @contextmenu="emit('contextmenu', game, $event)"
      @keydown="emit('contextmenu', game, $event)"
    >
      <td class="library-table-title">
        <div class="flex items-center gap-2 min-w-0">
          <span v-if="selected" class="row-selection-mark" aria-hidden="true"><IconCheck class="w-3 h-3" stroke-width="3" /></span>
          <span v-if="gameCoverSrc(game)" class="cover-thumb w-8 h-8 shrink-0">
            <CoverImage :game="game" img-class="w-8 h-8 rounded object-cover" />
          </span>
          <CoverPlaceholder v-else :title="game.title" class="w-8 h-8 rounded shrink-0" />
          <button
            @click.stop="emit('activate', game, $event)"
            class="truncate text-left font-semibold"
            :title="game.title"
          >
            {{ game.title }}
          </button>
          <span v-if="wishlisted" class="wishlist-pill shrink-0">Wishlist</span>
          <span v-if="game.thread_completed" class="finished-pill shrink-0">Finished</span>
          <span v-if="game.thread_onhold === 1" class="onhold-pill shrink-0">On hold</span>
          <span v-if="game.thread_abandoned === 1" class="abandoned-pill shrink-0">Abandoned</span>
          <IconHeartFilled
            v-if="game.is_favorite"
            class="w-3.5 h-3.5 shrink-0"
            style="color: var(--rating-accent)"
            role="img"
            aria-label="Favorite"
          />
          <span
            v-if="running"
            class="running-label shrink-0"
            ><IconPointFilled class="w-2.5 h-2.5" aria-hidden="true" />Running</span
          >
          <IconLoader2
            v-if="game.metadata_pending"
            class="w-3.5 h-3.5 shrink-0 animate-spin"
            style="color: var(--text-muted)"
            role="img"
            aria-label="Fetching F95 info"
          />
        </div>
      </td>
      <td>
        <div class="flex items-center gap-1">
          <span class="font-mono text-xs">{{
            game.version || "Unknown"
          }}</span>
          <span
            v-if="updateAvailable"
            class="update-marker"
            :title="`Update available: ${game.latest_version}`"
            ><IconArrowBigUpFilled class="update-marker-icon" aria-hidden="true" /><span
              >Update<span class="sr-only"> available:</span></span
            ><span class="update-marker-version">{{ game.latest_version }}</span></span
          >
          <span v-if="unplayed" class="unplayed-label">Unplayed</span>
        </div>
        <div v-if="moveState" class="row-move mt-1" role="status">
          <span class="row-move-label">{{ moveState === "moving" ? `Moving ${movePercent ?? 0}%` : "Waiting" }}</span>
          <span v-if="moveState === 'moving'" class="row-move-track" aria-hidden="true"><span class="row-move-fill" :style="{ width: `${movePercent ?? 0}%` }"></span></span>
        </div>
      </td>
      <td class="library-table-clip" :title="playStatus.label">
        <span
          class="ui-status-inline whitespace-nowrap"
          :class="playStatus.toneClass"
          ><component
            :is="playStatus.icon"
            class="ui-status-icon"
            aria-hidden="true"
          />{{ playStatus.label }}</span
        >
      </td>
      <td
        class="col-engine library-table-clip text-xs"
        :title="game.engine || 'Unknown'"
      >
        {{ game.engine || "Unknown" }}
      </td>
      <td class="text-xs">
        <template v-if="playtimeText">{{ playtimeText }}</template>
        <span v-else class="row-empty" title="Not played yet"><span aria-hidden="true">—</span><span class="sr-only">Not played yet</span></span>
      </td>
      <td
        class="col-last-played text-xs"
        :title="game.last_played || 'Not played yet'"
      >
        <template v-if="lastPlayedText">{{ lastPlayedText }}</template>
        <span v-else class="row-empty"><span aria-hidden="true">—</span><span class="sr-only">Not played yet</span></span>
      </td>
      <td>
        <div class="flex items-center gap-1">
          <button
            @click.stop="emit('toggle-game', game)"
            :disabled="!!moveState"
            :title="moveState ? 'This game is being moved. Wait for the move to finish.' : playLabel"
            class="play-btn rounded-md p-1.5"
            :class="[wishlisted ? 'ui-setup-btn' : 'ui-play-btn', { 'play-quiet': quietPlay }]"
            :aria-label="playLabel"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="w-4 h-4"
              aria-hidden="true"
            />
            <IconDownload v-else-if="wishlisted" class="w-4 h-4" aria-hidden="true" />
            <IconPlayerPlayFilled v-else class="w-4 h-4" aria-hidden="true" />
          </button>
          <select
            v-if="additionalTargets"
            :aria-label="`Choose launch target for ${game.title}`"
            :title="`Choose launch target for ${game.title}`"
            class="table-target-select"
            @click.stop
            @change="emit('target-select', game, $event)"
          >
            <option value="">▾</option>
            <option
              v-for="target in effectiveTargets"
              :key="target.id"
              :value="target.id"
            >
              {{ target.label }}
            </option>
          </select>
        </div>
      </td>
    </tr>
</template>

<style scoped>
.is-selected { box-shadow: inset 0 0 0 3px var(--brand); }
.running-label {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  background: var(--brand);
  color: var(--on-brand);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.75rem;
  font-weight: 700;
}
.row-selection-mark {
  display: inline-flex;
  flex-shrink: 0;
  padding: 0.2rem;
  border-radius: 999px;
  background: var(--brand);
  color: var(--on-brand);
}
.row-empty {
  color: var(--text-muted);
}
.wishlist-pill,
.finished-pill,
.onhold-pill,
.abandoned-pill {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
}
.finished-pill {
  color: var(--success-text);
}
.onhold-pill {
  color: var(--warning-text);
}
.abandoned-pill {
  color: var(--danger-text);
}
td {
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
button:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
/* Fits the longest built-in status ("Waiting For Update"); longer custom names truncate. */
.library-table-clip {
  max-width: 10.5rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
.library-table-title {
  max-width: 16rem;
  min-width: 10rem;
  color: var(--text-primary);
}
/* Same thresholds as the header cells in LibraryView (the container is .library-table-wrap). */
@container (max-width: 780px) {
  .col-engine {
    display: none;
  }
}
@container (max-width: 690px) {
  .col-last-played {
    display: none;
  }
}
.game-row {
  height: 48px;
  cursor: pointer;
}
.game-row:hover {
  background: var(--bg-raised);
}
/* Inset, so the table edge and the next row never clip the ring. */
.game-row:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: -2px;
}
.game-row.is-selected:focus-visible {
  outline-color: var(--text-primary);
}
/* A running row is tinted and its title carries the "Running" label; no side bar. */
.game-row.is-running {
  background: var(--brand-glow);
}
.play-btn {
  transition: background-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, color var(--motion-fast) ease;
}
/* Same rule as the cards: Play is quiet until the row is hovered or focused. */
@media (hover: hover) and (pointer: fine) {
  .game-row:not(:hover):not(:focus-within) .play-btn.play-quiet {
    background: var(--bg-raised);
    box-shadow: inset 0 0 0 1px var(--border-hover);
    color: var(--success-text);
  }
}
.table-target-select {
  width: 1.5rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  border-radius: 0.25rem;
}
/* Same marker as the card covers: solid tag, icon and the word "Update". */
.update-marker {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.15rem 0.4rem 0.15rem 0.25rem;
  border-radius: 0.3rem;
  background: var(--success-text);
  color: var(--text-inverse);
  font-size: 0.75rem;
  font-weight: 800;
  line-height: 1.2;
  white-space: nowrap;
}
.update-marker-icon {
  width: 0.85rem;
  height: 0.85rem;
  flex-shrink: 0;
}
.update-marker-version {
  max-width: 7rem;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600;
}
.unplayed-label {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
}
.row-move { display: flex; align-items: center; gap: 0.5rem; }
.row-move-label { font-size: 0.75rem; font-weight: 600; color: var(--brand); white-space: nowrap; }
.row-move-track { flex: 1; max-width: 8rem; height: 0.25rem; border-radius: 9999px; background: var(--bg-inset); overflow: hidden; }
.row-move-fill { display: block; height: 100%; background: var(--brand); transition: width 0.4s ease; }
</style>
