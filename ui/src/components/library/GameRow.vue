<script setup lang="ts">
// SPDX-License-Identifier: GPL-3.0-or-later
// One list row. Props are primitives and handlers are emitted with the game, so
// the parent can bind method references and Vue skips this row when only
// another game changed.
import { computed } from "vue";
import {
  IconArrowUp,
  IconLoader2,
  IconPhotoFilled,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconStarFilled,
} from "@tabler/icons-vue";
import CoverImage from "../CoverImage.vue";
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
  primary: boolean;
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
const playtimeText = computed(() => formatPlaytime(props.game.playtime_seconds));
const lastPlayedText = computed(() => formatLastPlayed(props.game.last_played));
const additionalTargets = computed(() => hasAdditionalLaunchTargets(props.game, props.running));
const effectiveTargets = computed(() => (additionalTargets.value ? getEffectiveLaunchTargets(props.game) : []));
</script>

<template>
    <tr
      :data-game-id="game.id"
      :data-controller-primary="primary ? '' : undefined"
      tabindex="0"
      role="row"
      @keydown.enter.self.prevent="emit('activate', game, $event)"
      @keydown.space.self.prevent="emit('activate', game, $event)"
      class="game-row"
      :class="{ 'is-running': running, 'is-selected': selected }"
      :aria-selected="selected"
      @mousedown.shift.prevent
      @click="emit('activate', game, $event)"
      @contextmenu="emit('contextmenu', game, $event)"
      @keydown="emit('contextmenu', game, $event)"
    >
      <td class="library-table-title"><span v-if="selected" class="mr-2" aria-hidden="true">✓</span>
        <div class="flex items-center gap-2 min-w-0">
          <span v-if="gameCoverSrc(game)" class="cover-thumb w-8 h-8 shrink-0">
            <CoverImage :game="game" img-class="w-8 h-8 rounded object-cover" />
          </span>
          <span
            v-else
            class="w-8 h-8 rounded shrink-0 flex items-center justify-center"
            style="background: var(--bg-raised)"
            aria-hidden="true"
            ><IconPhotoFilled
              class="w-4 h-4"
              style="color: var(--text-muted)"
          /></span>
          <button
            @click.stop="emit('activate', game, $event)"
            class="truncate text-left font-semibold"
            :title="game.title"
          >
            {{ game.title }}
          </button>
          <span v-if="wishlisted" class="wishlist-pill shrink-0">Wishlist</span>
          <span v-if="wishlisted && game.thread_completed" class="finished-pill shrink-0">Finished</span>
          <IconStarFilled
            v-if="game.is_favorite"
            class="w-3.5 h-3.5 shrink-0 text-yellow-500"
            aria-label="Favorite"
          />
          <span
            v-if="running"
            class="running-label shrink-0"
            >Running</span
          >
          <IconLoader2
            v-if="game.metadata_pending"
            class="w-3.5 h-3.5 shrink-0 animate-spin"
            style="color: var(--text-muted)"
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
            class="update-pill"
            :title="`Update available: ${game.latest_version}`"
          >
            <IconArrowUp class="w-3 h-3" aria-hidden="true" />
            <span class="sr-only">Update available: </span
            >{{ game.latest_version }}
          </span>
          <span v-if="unplayed" class="unplayed-label">Unplayed</span>
        </div>
      </td>
      <td class="library-table-clip" :title="playStatus.label">
        <span
          class="ui-status-inline whitespace-nowrap"
          :class="playStatus.toneClass"
          ><component
            :is="playStatus.icon"
            class="ui-status-icon"
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
        {{ playtimeText }}
      </td>
      <td
        class="col-last-played text-xs"
        :title="game.last_played || 'Never'"
      >
        {{ lastPlayedText }}
      </td>
      <td>
        <div class="flex items-center gap-1">
          <button
            @click.stop="emit('toggle-game', game)"
            :title="`${wishlisted ? 'Set up' : running ? 'Stop' : 'Play'} ${game.title}`"
            class="play-btn ui-play-btn rounded-md p-1.5"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="w-4 h-4"
            /><span v-else-if="wishlisted" class="text-xs font-semibold">Set up</span><IconPlayerPlayFilled v-else class="w-4 h-4" />
          </button>
          <select
            v-if="additionalTargets"
            aria-label="Choose launch target"
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
  background: var(--brand);
  color: var(--on-brand);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
}
.wishlist-pill,
.finished-pill {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 1.2;
}
.finished-pill {
  color: var(--success-text);
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
.game-row.is-running {
  background: var(--brand-glow);
  box-shadow: inset 3px 0 var(--brand);
}
.table-target-select {
  width: 1.5rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  border-radius: 0.25rem;
}
.update-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  color: var(--success-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  white-space: nowrap;
}
.unplayed-label {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
</style>
