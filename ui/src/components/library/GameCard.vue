<script setup lang="ts">
// SPDX-License-Identifier: GPL-3.0-or-later
// One library card (grid, compact and banner). Props are primitives and handlers are emitted with the game, so
// the parent can bind method references and Vue skips this card when only
// another game changed.
import { computed } from "vue";
import {
  IconArrowUp,
  IconChevronDown,
  IconDeviceGamepad2,
  IconHeartFilled,
  IconLoader2,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconRefresh,
} from "@tabler/icons-vue";
import CoverImage from "../CoverImage.vue";
import type { GameRecord } from "../../services/api";
import type { LayoutMode } from "../../utils/libraryViewState";
import { gameCoverSrc } from "../../utils/gameCover";
import { titleInitials, titleSwatch } from "../../utils/coverPlaceholder";
import {
  formatPlaytime,
  getEffectiveLaunchTargets,
  type EffectiveLaunchTarget,
  getGamePlayStatusMeta,
  hasAdditionalLaunchTargets,
  hasAvailableUpdate,
  hasUnplayedUpdate,
  isWishlisted,
} from "../../utils/libraryGames";


const props = defineProps<{
  game: GameRecord;
  layoutMode: LayoutMode;
  selected: boolean;
  running: boolean;
  /** "moving" or "waiting" while the game is in the move queue. */
  moveState?: "" | "moving" | "waiting";
  movePercent?: number;
  menuOpen: boolean;
  updating: boolean;
  updateBusy: boolean;
  selectionMode: boolean;
  primary: boolean;
}>();
const emit = defineEmits<{
  activate: [game: GameRecord, event: MouseEvent | KeyboardEvent];
  contextmenu: [game: GameRecord, event: MouseEvent | KeyboardEvent];
  "toggle-game": [game: GameRecord];
  "toggle-menu": [game: GameRecord];
  "launch-target": [game: GameRecord, target: EffectiveLaunchTarget];
  "check-update": [game: GameRecord];
}>();

// Compact and banner both draw image-only tiles (cover, overlay, bottom row).
const isImageTileLayout = computed(() => props.layoutMode === "compact" || props.layoutMode === "banner");
const placeholderInitials = computed(() => titleInitials(props.game.title));
const placeholderSwatch = computed(() => titleSwatch(props.game.title));
const wishlisted = computed(() => isWishlisted(props.game));
const updateAvailable = computed(() => hasAvailableUpdate(props.game));
const unplayed = computed(() => hasUnplayedUpdate(props.game));
const playStatus = computed(() => getGamePlayStatusMeta(props.game));
const playtimeText = computed(() => formatPlaytime(props.game.playtime_seconds));
const additionalTargets = computed(() => hasAdditionalLaunchTargets(props.game, props.running));
const effectiveTargets = computed(() => (props.menuOpen ? getEffectiveLaunchTargets(props.game) : []));
</script>

<template>
  <div
    :data-game-id="game.id"
    :data-controller-primary="primary ? '' : undefined"
    @click="emit('activate', game, $event)"
    @keydown.enter.self.prevent="emit('activate', game, $event)"
    @keydown.space.self.prevent="emit('activate', game, $event)"
    @keydown="emit('contextmenu', game, $event)"
    @contextmenu="emit('contextmenu', game, $event)"
    tabindex="0"
    role="checkbox"
    :aria-label="`${game.title}. ${selectionMode ? 'Press Enter to toggle selection.' : 'Press Enter for details.'}`"
    :aria-checked="selected"
    @mousedown.shift.prevent
    class="game-card group rounded-xl cursor-pointer relative"
    :class="[
      layoutMode === 'banner'
        ? 'aspect-[2/1]'
        : isImageTileLayout
          ? 'aspect-[16/9]'
          : 'flex flex-col',
      {
        'is-selected': selected,
        'is-running': running,
        'is-menu-open': menuOpen,
      },
    ]"
  >
    <span v-if="selected" class="selection-mark" aria-hidden="true">✓</span>
    <div v-if="moveState" class="card-move" role="status">
      <span class="card-move-label">{{ moveState === "moving" ? `Moving ${movePercent ?? 0}%` : "Waiting" }}</span>
      <span class="card-move-track" aria-hidden="true"><span class="card-move-fill" :style="{ width: `${moveState === 'moving' ? (movePercent ?? 0) : 0}%` }"></span></span>
    </div>
    <div
      class="flex items-center justify-center relative shadow-inner shrink-0 rounded-t-xl"
      :class="
        isImageTileLayout
          ? 'w-full h-full rounded-b-xl'
          : 'w-full aspect-[16/9]'
      "
      style="
        background: linear-gradient(
          135deg,
          var(--bg-raised),
          var(--bg-inset)
        );
      "
    >
      <CoverImage
        v-if="gameCoverSrc(game)"
        :game="game"
        :alt="game.title"
        :img-class="[
          'absolute inset-0 w-full h-full object-cover rounded-t-xl',
          layoutMode === 'banner' ? 'object-center' : 'object-top',
          isImageTileLayout ? 'rounded-b-xl' : '',
        ]"
      />
      <div
        v-else
        class="cover-placeholder absolute inset-0"
        :class="isImageTileLayout ? 'rounded-xl' : 'rounded-t-xl'"
        :data-tag-color="placeholderSwatch"
        aria-hidden="true"
      >
        <span v-if="placeholderInitials" class="cover-placeholder-initials">{{ placeholderInitials }}</span>
        <IconDeviceGamepad2 v-else class="cover-placeholder-icon" />
      </div>
      <div
        v-if="game.metadata_pending"
        class="metadata-pending absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-xs font-medium"
        :class="isImageTileLayout ? 'rounded-xl' : 'rounded-t-xl'"
        role="status"
      >
        <IconLoader2 class="w-6 h-6 animate-spin" />
        Fetching F95 info…
      </div>
      <div class="absolute top-2 left-2 flex items-center gap-1 z-10">
        <span
          v-if="running"
          class="running-label"
          title="Game is running"
          >● Running</span
        >
        <span
          v-if="game.is_favorite"
          class="card-indicator"
          title="Favorite"
          role="img"
          aria-label="Favorite"
          ><IconHeartFilled class="favorite-marker w-3 h-3" aria-hidden="true"
        /></span>
        <span
          v-if="isImageTileLayout && updateAvailable"
          class="update-pill update-pill-on-art"
          :title="`Update available: ${game.latest_version}`"
        >
          <IconArrowUp class="w-3 h-3" aria-hidden="true" />
          <span class="sr-only">Update available: </span
          >{{ game.latest_version }}
        </span>
        <span v-if="isImageTileLayout && unplayed" class="unplayed-label unplayed-on-art">Unplayed</span>
      </div>
      <button
        v-if="!isImageTileLayout && game.f95_url"
        @click.stop="emit('check-update', game)"
        :disabled="updateBusy"
        :title="`Check updates for ${game.title}`"
        class="update-overlay-btn ui-icon-btn absolute top-2 right-2 p-1.5 rounded-lg"
        :class="{ 'update-check-running': updating }"
      >
        <IconRefresh
          v-if="!updating"
          class="ui-action-icon"
        /><IconLoader2 v-else class="ui-action-icon animate-spin" />
      </button>
      <span
        v-if="!isImageTileLayout && game.rating"
        class="rating-badge absolute bottom-2 left-2 text-xs font-bold px-2 py-0.5 rounded flex items-center gap-1"
        role="img"
        :aria-label="`F95 rating ${game.rating}`"
        :title="`F95 rating ${game.rating}`"
        ><span class="rating-badge-source" aria-hidden="true">F95</span><span aria-hidden="true">{{ game.rating }}</span></span
      >
      <div
        v-if="isImageTileLayout"
        class="compact-image-overlay banner-reveal absolute inset-0 rounded-xl"
      ></div>
      <div
        v-if="isImageTileLayout"
        class="banner-reveal absolute inset-x-0 bottom-0 z-10 p-2 flex items-end gap-2"
        :class="layoutMode === 'banner' ? 'justify-start' : 'justify-between'"
      >
        <h3
          class="compact-title text-sm font-bold leading-tight"
          :class="
            layoutMode === 'banner'
              ? 'banner-title min-w-0 flex-1'
              : ''
          "
          :title="game.title"
        >
          {{ game.title }}
        </h3>
        <div
          class="relative flex shrink-0"
          :class="{ 'order-first': layoutMode === 'banner' }"
        >
          <button
            @click.stop="emit('toggle-game', game)"
            :disabled="!!moveState"
            class="compact-play-btn ui-play-btn rounded-md p-2"
            :title="moveState ? 'This game is being moved. Wait for the move to finish.' : `${wishlisted ? 'Set up' : running ? 'Stop' : 'Play'} ${game.title}`"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="w-3.5 h-3.5"
            /><span v-else-if="wishlisted" class="text-xs font-semibold">Set up</span><IconPlayerPlayFilled v-else class="w-3.5 h-3.5" />
          </button>
          <button
            v-if="additionalTargets"
            @click.stop="emit('toggle-menu', game)"
            class="compact-target-toggle rounded-md px-1.5"
            :title="`Choose launch target for ${game.title}`"
            :aria-expanded="menuOpen"
          >
            <IconChevronDown class="w-3.5 h-3.5" />
          </button>
          <div
            v-if="menuOpen && !running"
            class="launch-target-menu"
            @click.stop
          >
            <button
              v-for="target in effectiveTargets"
              :key="target.id"
              class="launch-target-menu-item"
              @click.stop="emit('launch-target', game, target)"
            >
              {{ target.label }}
            </button>
          </div>
        </div>
      </div>
    </div>
    <div
      v-if="!isImageTileLayout"
      class="p-3 flex flex-col flex-1 min-w-0"
    >
      <h3
        class="font-bold truncate text-base mb-1"
        :title="game.title"
        style="color: var(--text-primary)"
      >
        {{ game.title }}
      </h3>
      <p
        v-if="game.developer"
        class="truncate text-xs mb-2"
        style="color: var(--text-muted)"
      >
        by {{ game.developer }}
      </p>
      <div class="flex flex-wrap items-center gap-1.5 text-xs mb-3">
        <span class="font-mono" style="color: var(--brand)">{{
          game.version || "Unknown"
        }}</span>
        <span v-if="wishlisted" class="wishlist-pill">Wishlist</span>
        <span v-if="wishlisted && game.thread_completed" class="finished-pill">Finished</span>
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
        <span
          v-if="game.playtime_seconds"
          style="color: var(--text-secondary)"
          >⏱ {{ playtimeText }}</span
        >
      </div>
      <div class="flex items-center justify-between gap-2 mt-auto">
        <span
          class="ui-status-chip px-2 py-1 text-xs min-w-0"
          :class="playStatus.toneClass"
          :title="playStatus.label"
          ><component
            :is="playStatus.icon"
            class="ui-status-icon"
          /><span class="truncate">{{
            playStatus.label
          }}</span></span
        >
        <div class="relative flex shrink-0">
          <button
            @click.stop="emit('toggle-game', game)"
            :disabled="!!moveState"
            :title="moveState ? 'This game is being moved. Wait for the move to finish.' : `${wishlisted ? 'Set up' : running ? 'Stop' : 'Play'} ${game.title}`"
            class="play-btn ui-play-btn ui-action-btn px-2.5 py-1.5 rounded-lg text-xs font-bold"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="ui-action-icon"
            /><IconPlayerPlayFilled v-else-if="!wishlisted" class="ui-action-icon" />{{
              wishlisted ? "Set up" : running ? "Stop" : "Play"
            }}
          </button>
          <button
            v-if="additionalTargets"
            @click.stop="emit('toggle-menu', game)"
            class="target-menu-toggle ui-action-btn px-1 py-1.5 rounded-lg"
            :title="`Choose launch target for ${game.title}`"
            :aria-expanded="menuOpen"
          >
            <IconChevronDown class="ui-action-icon" />
          </button>
          <div
            v-if="menuOpen && !running"
            class="launch-target-menu"
            @click.stop
          >
            <button
              v-for="target in effectiveTargets"
              :key="target.id"
              class="launch-target-menu-item"
              @click.stop="emit('launch-target', game, target)"
            >
              {{ target.label }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.is-selected { box-shadow: inset 0 0 0 3px var(--brand); }
.selection-mark { position: absolute; z-index: 5; top: .5rem; right: .5rem; padding: .2rem .45rem; border-radius: 999px; background: var(--brand); color: var(--on-brand); font-weight: bold; }
.banner-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  text-overflow: ellipsis;
  overflow: hidden;
}
/* Hidden only visually: opacity keeps Play clickable and focusable, so a click
   that arrives before :hover applies still hits the button, not the card. */
.library-cards--banner .banner-reveal {
  opacity: 0;
  transition: opacity 0.15s ease;
}
.library-cards--banner .game-card:hover .banner-reveal,
.library-cards--banner .game-card:focus-within .banner-reveal,
.library-cards--banner .game-card.is-running .banner-reveal,
.library-cards--banner .game-card.is-menu-open .banner-reveal {
  opacity: 1;
}
@media (hover: none) {
  .library-cards--banner .banner-reveal {
    opacity: 1;
  }
}
/* Play sits bottom-left here, so the chooser opens rightward from it. */
.library-cards--banner .launch-target-menu {
  right: auto;
  left: 0;
}
.game-card:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
.metadata-pending {
  background: color-mix(in srgb, var(--bg-surface) 82%, transparent);
  color: var(--text-secondary);
}

.game-card.is-running {
  border-color: var(--brand);
  box-shadow:
    0 0 0 2px var(--brand),
    var(--shadow-card);
}
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
.card-indicator {
  background: var(--overlay-scrim-strong);
  border-radius: 0.3rem;
  padding: 0.25rem;
}
/* The marker sits on the dark scrim in both themes, so it keeps the bright yellow
   (the light theme darkens the yellow-400 utility for white surfaces). */
.favorite-marker {
  color: #facc15;
}
.game-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.game-card:hover {
  border-color: var(--brand);
  box-shadow: 0 4px 20px var(--brand-glow);
  transform: translateY(-2px);
}

:global(.motion-off) .game-card:not(.is-running):hover {
  border-color: var(--border);
  box-shadow: var(--shadow-card);
  transform: none;
}

.target-menu-toggle {
  margin-left: 0.35rem;
  background: var(--bg-raised);
  border: 1px solid var(--border-hover);
  color: var(--text-secondary);
}

.target-menu-toggle:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}

.launch-target-menu {
  position: absolute;
  z-index: 30;
  right: 0;
  bottom: calc(100% + 0.5rem);
  min-width: 11rem;
  max-width: 16rem;
  padding: 0.35rem;
  border: 1px solid var(--border-hover);
  border-radius: 0.5rem;
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.launch-target-menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  width: 100%;
  padding: 0.45rem 0.55rem;
  border-radius: 0.4rem;
  color: var(--text-primary);
  font-size: 0.75rem;
  text-align: left;
}

.launch-target-menu-item:hover {
  background: var(--bg-raised);
}

.compact-image-overlay {
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.85) 0%,
    rgba(0, 0, 0, 0.58) 42%,
    rgba(0, 0, 0, 0.24) 70%,
    rgba(0, 0, 0, 0.08) 100%
  );
}

.compact-title {
  color: #f8fafc;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.95),
    0 0 8px rgba(0, 0, 0, 0.85);
  -webkit-text-stroke: 0.4px rgba(0, 0, 0, 0.65);
  max-height: 2.6em;
  overflow: hidden;
}

.compact-target-toggle {
  margin-left: 0.25rem;
  background: rgba(17, 24, 39, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #f8fafc;
}

.compact-target-toggle:hover {
  background: rgba(17, 24, 39, 0.78);
  border-color: rgba(255, 255, 255, 0.42);
}

/* Sits on cover art in both themes, so it keeps dark-on-image colors. */
.rating-badge {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: #facc15;
}

.rating-badge-source {
  color: var(--overlay-control-text);
  font-weight: 600;
  letter-spacing: 0.02em;
}

/* Initials on a tag swatch for games without a cover; the swatch tokens pass 4.5:1 in both themes. */
.cover-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  container-type: size;
  background: var(--tag-bg);
  color: var(--tag-text);
}

.cover-placeholder-initials {
  font-size: clamp(1rem, 32cqmin, 4.5rem);
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1;
}

.cover-placeholder-icon {
  width: clamp(1.5rem, 34cqmin, 4rem);
  height: clamp(1.5rem, 34cqmin, 4rem);
}

.update-overlay-btn {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: var(--overlay-control-text);
  /* Hidden only visually: it stays clickable so a click that arrives with the
     pointer (before :hover applies) still hits the button, not the card. */
  opacity: 0;
  transition: opacity 0.18s ease, background-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

.game-card:hover .update-overlay-btn,
.game-card:focus-within .update-overlay-btn,
/* Every card's control is disabled while any check runs; only the card being
   checked keeps its spinner visible at rest. */
.update-overlay-btn.update-check-running {
  opacity: 1;
}

@media (hover: none) {
  .update-overlay-btn {
    opacity: 1;
  }
}

.update-overlay-btn:hover {
  background: var(--overlay-scrim);
  color: var(--overlay-control-text);
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

/* Sits on cover art, so it keeps a solid fill for contrast. */
.update-pill-on-art {
  background: var(--success-text);
  border-color: transparent;
  color: var(--text-inverse);
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
.unplayed-on-art {
  background: var(--bg-surface);
}
.card-move {
  position: absolute; top: 0.5rem; left: 0.5rem; right: 0.5rem; z-index: 5;
  display: flex; flex-direction: column; gap: 0.25rem; padding: 0.35rem 0.5rem;
  border-radius: 0.5rem; background: var(--bg-surface); border: 1px solid var(--border-hover);
  box-shadow: var(--shadow-card); pointer-events: none;
}
.card-move-label { font-size: 0.7rem; font-weight: 700; color: var(--text-primary); }
.card-move-track { display: block; height: 0.25rem; border-radius: 9999px; background: var(--bg-inset); overflow: hidden; }
.card-move-fill { display: block; height: 100%; background: var(--brand); transition: width 0.4s ease; }
</style>
