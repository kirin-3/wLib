<script setup lang="ts">
// SPDX-License-Identifier: GPL-3.0-or-later
// One library card (grid, compact and banner). Props are primitives and handlers are emitted with the game, so
// the parent can bind method references and Vue skips this card when only
// another game changed.
import { computed } from "vue";
import {
  IconArrowBigUpFilled,
  IconCheck,
  IconChevronDown,
  IconClock,
  IconHeartFilled,
  IconLoader2,
  IconDownload,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconPointFilled,
  IconRefresh,
  IconStarFilled,
} from "@tabler/icons-vue";
import CoverImage from "../CoverImage.vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import type { GameRecord } from "../../services/api";
import type { LayoutMode } from "../../utils/libraryViewState";
import { gameCoverSrc } from "../../utils/gameCover";
import { updateCheckUrl } from "../../utils/lewdcorner";
import {
  formatCardRating,
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
  /** Position in the games grid for assistive technology (the first card is 1). */
  rowIndex: number;
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
const wishlisted = computed(() => isWishlisted(props.game));
const updateAvailable = computed(() => hasAvailableUpdate(props.game));
const unplayed = computed(() => hasUnplayedUpdate(props.game));
const playStatus = computed(() => getGamePlayStatusMeta(props.game));
const playtimeText = computed(() => formatPlaytime(props.game.playtime_seconds));
const rating = computed(() => (isImageTileLayout.value ? null : formatCardRating(props.game)));
// Play stays quiet until the card is hovered or focused; Stop and Set up keep their own look.
const quietPlay = computed(() => !props.running && !wishlisted.value);
const additionalTargets = computed(() => hasAdditionalLaunchTargets(props.game, props.running));
const effectiveTargets = computed(() => (props.menuOpen ? getEffectiveLaunchTargets(props.game) : []));
const playVerb = computed(() => (wishlisted.value ? "Set up" : props.running ? "Stop" : "Play"));
const playLabel = computed(() => `${playVerb.value} ${props.game.title}`);
const playTitle = computed(() => (props.moveState ? "This game is being moved. Wait for the move to finish." : playLabel.value));
const targetMenuId = computed(() => `launch-targets-${props.game.id}`);
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
    role="row"
    :aria-rowindex="rowIndex"
    :aria-label="game.title"
    :aria-selected="selectionMode ? selected : undefined"
    aria-describedby="library-item-hint"
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
    <span v-if="selected" class="selection-mark" aria-hidden="true"><IconCheck class="w-3.5 h-3.5" stroke-width="3" /></span>
    <!-- The card is a grid row; the cover and the details are its cells, so the
         buttons inside are valid (a row may hold interactive cells). -->
    <div
      role="gridcell"
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
      <div v-if="moveState" class="card-move" role="status">
        <span class="card-move-label">{{ moveState === "moving" ? `Moving ${movePercent ?? 0}%` : "Waiting" }}</span>
        <span class="card-move-track" aria-hidden="true"><span class="card-move-fill" :style="{ width: `${moveState === 'moving' ? (movePercent ?? 0) : 0}%` }"></span></span>
      </div>
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
      <CoverPlaceholder
        v-else
        :title="game.title"
        class="absolute inset-0"
        :class="isImageTileLayout ? 'rounded-xl' : 'rounded-t-xl'"
      />
      <div
        v-if="game.metadata_pending"
        class="metadata-pending absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-xs font-medium"
        :class="isImageTileLayout ? 'rounded-xl' : 'rounded-t-xl'"
        role="status"
      >
        <IconLoader2 class="w-6 h-6 animate-spin" aria-hidden="true" />
        Fetching F95 info…
      </div>
      <!-- Stops short of the top-right corner, where the selection mark and the update check sit. -->
      <div class="cover-badges">
        <span
          v-if="running"
          class="running-label"
          title="Game is running"
          ><IconPointFilled class="w-2.5 h-2.5" aria-hidden="true" />Running</span
        >
        <span
          v-if="updateAvailable"
          class="update-marker"
          :title="`Update available: ${game.latest_version}`"
          ><IconArrowBigUpFilled class="update-marker-icon" aria-hidden="true" /><span
            >Update<span class="sr-only"> available:</span></span
          ><span class="update-marker-version">{{ game.latest_version }}</span></span
        >
        <span
          v-if="game.is_favorite"
          class="card-indicator"
          title="Favorite"
          role="img"
          aria-label="Favorite"
          ><IconHeartFilled class="favorite-marker w-3 h-3" aria-hidden="true"
        /></span>
        <span v-if="isImageTileLayout && unplayed" class="unplayed-label unplayed-on-art">Unplayed</span>
      </div>
      <button
        v-if="!isImageTileLayout && !selected && updateCheckUrl(game)"
        @click.stop="emit('check-update', game)"
        :disabled="updateBusy"
        :title="`Check updates for ${game.title}`"
        :aria-label="updating ? `Checking updates for ${game.title}` : `Check updates for ${game.title}`"
        class="update-overlay-btn ui-icon-btn absolute top-2 right-2 p-1.5 rounded-lg"
        :class="{ 'update-check-running': updating }"
      >
        <IconRefresh
          v-if="!updating"
          class="ui-action-icon"
          aria-hidden="true"
        /><IconLoader2 v-else class="ui-action-icon animate-spin" aria-hidden="true" />
      </button>
      <span
        v-if="rating"
        class="rating-badge absolute bottom-2 left-2 text-xs font-bold px-1.5 py-0.5 rounded flex items-center gap-1"
        role="img"
        :aria-label="rating.label"
        :title="rating.label"
        ><IconStarFilled class="w-3 h-3" aria-hidden="true" /><span aria-hidden="true">{{ rating.value }}</span></span
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
            class="compact-play-btn rounded-md p-2"
            :class="[wishlisted ? 'ui-setup-btn' : 'ui-play-btn', { 'play-quiet': quietPlay }]"
            :aria-label="playLabel"
            :title="playTitle"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="w-3.5 h-3.5"
              aria-hidden="true"
            />
            <IconDownload v-else-if="wishlisted" class="w-3.5 h-3.5" aria-hidden="true" />
            <IconPlayerPlayFilled v-else class="w-3.5 h-3.5" aria-hidden="true" />
          </button>
          <button
            v-if="additionalTargets"
            @click.stop="emit('toggle-menu', game)"
            class="compact-target-toggle rounded-md px-1.5"
            :title="`Choose launch target for ${game.title}`"
            :aria-label="`Choose launch target for ${game.title}`"
            :aria-expanded="menuOpen"
            :aria-controls="menuOpen && !running ? targetMenuId : undefined"
          >
            <IconChevronDown class="w-3.5 h-3.5" aria-hidden="true" />
          </button>
          <div
            v-if="menuOpen && !running"
            :id="targetMenuId"
            class="launch-target-menu"
            role="group"
            :aria-label="`Launch targets for ${game.title}`"
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
      role="gridcell"
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
      <!-- One quiet line of facts; the update marker on the cover leads. -->
      <div class="card-meta mb-3">
        <span class="font-mono" style="color: var(--text-secondary)">{{
          game.version || "Unknown"
        }}</span>
        <span v-if="wishlisted" class="card-fact">Wishlist</span>
        <span v-if="game.thread_completed" class="card-fact card-fact--finished">Finished</span>
        <span v-if="game.thread_onhold === 1" class="card-fact card-fact--onhold">On hold</span>
        <span v-if="game.thread_abandoned === 1" class="card-fact card-fact--abandoned">Abandoned</span>
        <span v-if="unplayed" class="card-fact" title="The installed version has not been played yet">Unplayed</span>
        <span
          v-if="game.playtime_seconds"
          class="card-fact card-fact--muted"
          ><IconClock class="w-3 h-3" aria-hidden="true" /><span class="sr-only">Playtime </span>{{ playtimeText }}</span
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
            aria-hidden="true"
          /><span class="truncate">{{
            playStatus.label
          }}</span></span
        >
        <div class="relative flex shrink-0">
          <button
            @click.stop="emit('toggle-game', game)"
            :disabled="!!moveState"
            :title="playTitle"
            :aria-label="playLabel"
            class="play-btn ui-action-btn px-2.5 py-1.5 rounded-lg text-xs font-bold"
            :class="[wishlisted ? 'ui-setup-btn' : 'ui-play-btn', { 'play-quiet': quietPlay }]"
          >
            <IconPlayerStopFilled
              v-if="!wishlisted && running"
              class="ui-action-icon"
              aria-hidden="true"
            />
            <IconDownload v-else-if="wishlisted" class="ui-action-icon" aria-hidden="true" />
            <IconPlayerPlayFilled v-else class="ui-action-icon" aria-hidden="true" />{{ playVerb }}
          </button>
          <button
            v-if="additionalTargets"
            @click.stop="emit('toggle-menu', game)"
            class="target-menu-toggle ui-action-btn px-1 py-1.5 rounded-lg"
            :title="`Choose launch target for ${game.title}`"
            :aria-label="`Choose launch target for ${game.title}`"
            :aria-expanded="menuOpen"
            :aria-controls="menuOpen && !running ? targetMenuId : undefined"
          >
            <IconChevronDown class="ui-action-icon" aria-hidden="true" />
          </button>
          <div
            v-if="menuOpen && !running"
            :id="targetMenuId"
            class="launch-target-menu"
            role="group"
            :aria-label="`Launch targets for ${game.title}`"
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
.selection-mark { position: absolute; z-index: 15; top: .5rem; right: .5rem; display: inline-flex; padding: .3rem; border-radius: 999px; background: var(--brand); color: var(--on-brand); box-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
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
.cover-badges {
  position: absolute;
  z-index: 10;
  top: 0.5rem;
  left: 0.5rem;
  right: 2.75rem;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0.25rem;
  pointer-events: none;
}
.cover-badges > * {
  pointer-events: auto;
}
.running-label {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  background: var(--brand);
  color: var(--on-brand);
  border-radius: 0.3rem;
  padding: 0.2rem 0.4rem;
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
}
/* The update marker: a solid tag with an icon and the word "Update", so it reads
   without colour. It sits on cover art, so it keeps a solid fill in both themes. */
.update-marker {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  min-width: 0;
  max-width: 100%;
  padding: 0.2rem 0.45rem 0.2rem 0.3rem;
  border-radius: 0.3rem;
  background: var(--success-text);
  color: var(--text-inverse);
  font-size: 0.75rem;
  font-weight: 800;
  line-height: 1.2;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
}
.update-marker-icon {
  width: 0.9rem;
  height: 0.9rem;
  flex-shrink: 0;
}
.update-marker-version {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600;
}
/* Plain text facts with a dot between them: no boxes, so they stay behind the cover marker.
   Each dot sits in the gap before its fact. The dot of a fact that starts a line
   falls left of the box and is clipped, so a line never starts with a dot. */
.card-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: 0.8rem;
  row-gap: 0.15rem;
  overflow: hidden;
  font-size: 0.75rem;
  line-height: 1.3;
}
.card-meta > *::before {
  content: "·";
  content: "·" / "";
  display: inline-block;
  width: 0.8rem;
  margin-left: -0.8rem;
  text-align: center;
  color: var(--text-muted);
}
.card-fact {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  color: var(--text-primary);
  font-weight: 600;
  white-space: nowrap;
}
/* Cancel the fact's own gap after its dot. */
.card-fact::before {
  margin-right: -0.2rem;
}
.card-fact--muted {
  color: var(--text-secondary);
  font-weight: 400;
}
.card-fact--finished {
  color: var(--success-text);
}
.card-fact--onhold {
  color: var(--warning-text);
}
.card-fact--abandoned {
  color: var(--danger-text);
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
  border: 1px solid rgba(255, 255, 255, 0.28);
}

.compact-play-btn {
  transition: background-color var(--motion-fast) ease, box-shadow var(--motion-fast) ease, color var(--motion-fast) ease;
}

.compact-play-btn.ui-setup-btn {
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
}

.compact-target-toggle,
.compact-play-btn.ui-setup-btn {
  background: rgba(17, 24, 39, 0.62);
  color: #f8fafc;
}

.compact-target-toggle:hover,
.compact-play-btn.ui-setup-btn:hover:not(:disabled) {
  background: rgba(17, 24, 39, 0.78);
  color: #f8fafc;
}

.compact-target-toggle:hover {
  border-color: rgba(255, 255, 255, 0.42);
}

.compact-play-btn.ui-setup-btn:hover:not(:disabled) {
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.42);
}

/* Play is quiet at rest, so a full grid is not a wall of green buttons. It turns
   full strength when the card is hovered or focused (keyboard and controller
   focus land on the card), and stays full strength on touch screens. */
@media (hover: hover) and (pointer: fine) {
  .game-card:not(:hover):not(:focus-within):not(.is-menu-open) .play-btn.play-quiet {
    background: var(--bg-raised);
    box-shadow: inset 0 0 0 1px var(--border-hover);
    color: var(--text-primary);
  }
  .game-card:not(:hover):not(:focus-within):not(.is-menu-open) .play-btn.play-quiet svg {
    color: var(--success-text);
  }
  .game-card:not(:hover):not(:focus-within):not(.is-menu-open) .compact-play-btn.play-quiet {
    background: rgba(17, 24, 39, 0.62);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
    color: #f8fafc;
  }
}

/* Sits on cover art in both themes, so it keeps dark-on-image colors. */
.rating-badge {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: var(--overlay-control-text);
}
.rating-badge svg {
  color: #facc15;
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
.unplayed-on-art {
  background: var(--bg-surface);
}
.card-move {
  position: absolute; top: 0.5rem; left: 0.5rem; right: 0.5rem; z-index: 5;
  display: flex; flex-direction: column; gap: 0.25rem; padding: 0.35rem 0.5rem;
  border-radius: 0.5rem; background: var(--bg-surface); border: 1px solid var(--border-hover);
  box-shadow: var(--shadow-card); pointer-events: none;
}
.card-move-label { font-size: 0.75rem; font-weight: 700; color: var(--text-primary); }
.card-move-track { display: block; height: 0.25rem; border-radius: 9999px; background: var(--bg-inset); overflow: hidden; }
.card-move-fill { display: block; height: 100%; background: var(--brand); transition: width 0.4s ease; }
</style>
