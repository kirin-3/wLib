<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, useId } from "vue";
import { IconChartBar, IconChevronDown, IconFolderOpen } from "@tabler/icons-vue";
import type { StorageCategoryReport } from "../../services/api";
import { formatStorageBytes, groupStorageCategories } from "../../utils/storage";

const props = defineProps<{
  categories: StorageCategoryReport[];
  totalBytes: number;
}>();

const emit = defineEmits<{
  (event: "open-location", target: string): void;
}>();

const OPENABLE_CATEGORIES = new Set([
  "download_archives",
  "torrent_data",
  "proton_builds",
  "shared_wine_prefix",
  "save_backups",
  "web_player",
  "sandboxes",
  "removed_game_data",
  "cover_cache",
  "other_caches",
  "catalog",
  "logs_and_backups",
]);

// Five colour families (tokens in style.css, light and dark). Related
// categories share one family on purpose; each row's label names the part.
type ChartFamily = "games" | "downloads" | "wine" | "game-data" | "app-data";

const CATEGORY_FAMILY: Record<string, ChartFamily> = {
  games: "games",
  download_archives: "downloads",
  torrent_data: "downloads",
  proton_builds: "wine",
  wine_prefixes: "wine",
  shared_wine_prefix: "wine",
  save_backups: "game-data",
  web_player: "game-data",
  sandboxes: "game-data",
  removed_game_data: "game-data",
  cover_cache: "app-data",
  other_caches: "app-data",
  catalog: "app-data",
  logs_and_backups: "app-data",
};

// The largest categories get their own row and bar segment; small and empty
// ones fold into one "Other" row so the bar and the list stay readable.
const grouped = computed(() => groupStorageCategories(props.categories));
const OTHER_ID = "other";
const otherOpen = ref(false);
const otherListId = useId();

interface BarSegment {
  id: string;
  label: string;
  bytes: number;
  percent: number;
}

const barSegments = computed<BarSegment[]>(() => {
  const segments: BarSegment[] = grouped.value.main
    .filter((cat) => cat.bytes > 0)
    .map(({ id, label, bytes, percent }) => ({ id, label, bytes, percent }));
  if (grouped.value.otherBytes > 0) {
    segments.push({
      id: OTHER_ID,
      label: "Other",
      bytes: grouped.value.otherBytes,
      percent: grouped.value.otherPercent,
    });
  }
  return segments;
});

const barEl = ref<HTMLElement | null>(null);
// The bar segment the pointer is on: it shows the tooltip.
const hoveredId = ref("");
// The row the pointer or keyboard focus is on: it lights its bar segment.
const rowActiveId = ref("");
// One highlight for both: the segment and its row light up together.
const activeId = computed(() => hoveredId.value || rowActiveId.value);
// Tooltip anchor: the segment centre in pixels from the bar's left edge.
const tooltipX = ref(0);
const tooltipAlign = ref<"start" | "center" | "end">("center");

const hoveredSegment = computed(
  () => barSegments.value.find((seg) => seg.id === hoveredId.value) ?? null,
);

function showSegment(event: MouseEvent, id: string) {
  const bar = barEl.value;
  const segment = event.currentTarget;
  hoveredId.value = id;
  if (!bar || !(segment instanceof HTMLElement)) return;
  const barRect = bar.getBoundingClientRect();
  const segRect = segment.getBoundingClientRect();
  const centre = segRect.left - barRect.left + segRect.width / 2;
  tooltipX.value = centre;
  // Keep the label inside the card near the bar's ends.
  const edge = Math.min(120, barRect.width / 4);
  tooltipAlign.value = centre < edge ? "start" : centre > barRect.width - edge ? "end" : "center";
}

function clearSegment() {
  hoveredId.value = "";
}

/** A category row's segment: its own, or "Other" for a folded category. */
function activateRow(id: string) {
  rowActiveId.value = barSegments.value.some((seg) => seg.id === id) ? id : "";
}

function clearRow(event: FocusEvent | MouseEvent) {
  // Focus moving between a row's own controls keeps the highlight.
  const next = event.relatedTarget;
  const row = event.currentTarget;
  if (next instanceof Node && row instanceof HTMLElement && row.contains(next)) return;
  rowActiveId.value = "";
}

// "Other" is a neutral grey: it is many small parts, not one thing. A category
// the map does not know yet also takes the neutral, never a new hue.
const barColor = (id: string): string => {
  const family = CATEGORY_FAMILY[id];
  return family ? `var(--chart-${family})` : "var(--chart-other)";
};
</script>

<template>
  <section class="storage-card rounded-xl" aria-labelledby="storage-categories-heading">
    <h3 id="storage-categories-heading" class="ui-section-heading text-base font-semibold mb-4">
      <IconChartBar class="ui-section-icon" />
      <span>Where the space goes</span>
    </h3>

    <div v-if="totalBytes > 0" class="relative mb-4">
      <div
        v-if="hoveredSegment"
        class="storage-bar-tooltip pointer-events-none absolute bottom-full mb-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium tabular-nums"
        :class="`storage-bar-tooltip--${tooltipAlign}`"
        :style="{ left: `${tooltipX}px` }"
        role="tooltip"
      >
        <span
          class="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
          :style="{ backgroundColor: barColor(hoveredSegment.id) }"
        ></span>
        <span style="color: var(--text-primary)">{{ hoveredSegment.label }}</span>
        <span style="color: var(--text-secondary)">
          · {{ formatStorageBytes(hoveredSegment.bytes) }} ({{ hoveredSegment.percent.toFixed(1) }}%)
        </span>
      </div>
      <div
        ref="barEl"
        class="storage-bar flex h-3 w-full overflow-hidden rounded-full"
        aria-hidden="true"
        @mouseleave="clearSegment"
      >
        <div
          v-for="seg in barSegments"
          :key="seg.id"
          class="storage-bar-segment h-full transition-all duration-300"
          :class="{
            'storage-bar-segment--dim': activeId && activeId !== seg.id,
            'storage-bar-segment--active': activeId === seg.id,
          }"
          :data-segment="seg.id"
          :style="{
            flexGrow: seg.percent,
            backgroundColor: barColor(seg.id),
          }"
          @mouseenter="showSegment($event, seg.id)"
        ></div>
      </div>
    </div>

    <!-- A report where nothing was counted has no parts worth listing. -->
    <p v-if="totalBytes <= 0" class="text-sm" style="color: var(--text-muted)" data-categories-empty>
      The scan found no files to count. Check that your library folders are connected, then scan again.
    </p>

    <ul v-else class="storage-category-list text-sm">
      <li
        v-for="cat in grouped.main"
        :key="cat.id"
        class="storage-category-row"
        :class="{ 'storage-category-row--active': activeId === cat.id }"
        :data-category="cat.id"
        @mouseenter="activateRow(cat.id)"
        @mouseleave="clearRow"
        @focusin="activateRow(cat.id)"
        @focusout="clearRow"
      >
        <span
          class="storage-dot"
          :class="{ 'storage-dot--empty': cat.bytes <= 0 }"
          :style="{ '--dot-color': barColor(cat.id) }"
          aria-hidden="true"
        ></span>
        <span class="storage-category-name">
          <span class="storage-category-label truncate font-medium" style="color: var(--text-primary)">
            {{ cat.label }}
          </span>
          <span class="storage-percent tabular-nums text-xs">
            {{ cat.percent.toFixed(1) }}%
          </span>
          <span class="storage-size tabular-nums font-semibold text-right shrink-0">
            {{ formatStorageBytes(cat.bytes) }}
          </span>
        </span>
        <button
          v-if="OPENABLE_CATEGORIES.has(cat.id) && cat.bytes > 0"
          type="button"
          class="storage-icon-btn"
          :aria-label="`Open ${cat.label} folder`"
          :title="`Open ${cat.label} folder`"
          @click="emit('open-location', cat.id)"
        >
          <IconFolderOpen class="w-4 h-4" />
        </button>
        <span v-else class="storage-icon-slot" aria-hidden="true"></span>
      </li>

      <li
        v-if="grouped.other.length"
        class="storage-other"
        data-category-other
        @mouseenter="activateRow(OTHER_ID)"
        @mouseleave="clearRow"
        @focusin="activateRow(OTHER_ID)"
        @focusout="clearRow"
      >
        <button
          type="button"
          class="storage-category-row storage-other-toggle w-full text-left"
          :class="{ 'storage-category-row--active': activeId === OTHER_ID }"
          :aria-expanded="otherOpen"
          :aria-controls="otherListId"
          @click="otherOpen = !otherOpen"
        >
          <span class="storage-dot" :style="{ '--dot-color': barColor(OTHER_ID) }" aria-hidden="true"></span>
          <span class="storage-category-name">
            <span class="storage-category-label truncate font-medium" style="color: var(--text-primary)">
              Other ({{ grouped.other.length }})
            </span>
            <span class="storage-percent tabular-nums text-xs">
              {{ grouped.otherPercent.toFixed(1) }}%
            </span>
            <span class="storage-size tabular-nums font-semibold text-right shrink-0">
              {{ formatStorageBytes(grouped.otherBytes) }}
            </span>
          </span>
          <span class="storage-icon-slot inline-flex items-center justify-center" aria-hidden="true">
            <IconChevronDown class="storage-chevron w-4 h-4" :class="{ 'storage-chevron--open': otherOpen }" />
          </span>
        </button>
        <ul v-show="otherOpen" :id="otherListId" class="storage-other-list" :aria-label="`Other categories`">
          <li
            v-for="cat in grouped.other"
            :key="cat.id"
            class="storage-category-row"
            :data-category="cat.id"
          >
            <span
              class="storage-dot"
              :class="{ 'storage-dot--empty': cat.bytes <= 0 }"
          :style="{ '--dot-color': barColor(cat.id) }"
              aria-hidden="true"
            ></span>
            <span class="storage-category-name">
              <span class="storage-category-label truncate" style="color: var(--text-secondary)">
                {{ cat.label }}
              </span>
              <span class="storage-percent tabular-nums text-xs">
                {{ cat.percent.toFixed(1) }}%
              </span>
              <span class="storage-size tabular-nums text-right shrink-0" style="font-weight: 500">
                {{ formatStorageBytes(cat.bytes) }}
              </span>
            </span>
            <button
              v-if="OPENABLE_CATEGORIES.has(cat.id) && cat.bytes > 0"
              type="button"
              class="storage-icon-btn"
              :aria-label="`Open ${cat.label} folder`"
              :title="`Open ${cat.label} folder`"
              @click="emit('open-location', cat.id)"
            >
              <IconFolderOpen class="w-4 h-4" />
            </button>
            <span v-else class="storage-icon-slot" aria-hidden="true"></span>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 1.5rem;
}
/* A 2px card-coloured gap between segments keeps two neighbours of one
   colour family apart. Segments share the width by size; a tiny one keeps 4px. */
.storage-bar {
  gap: 2px;
}
.storage-bar-segment {
  flex: 0 1 0;
  min-width: 4px;
  cursor: default;
}
.storage-bar-segment--dim {
  opacity: 0.35;
}
/* Lit from its row: a thin ring in the card colour sets it apart from its neighbours. */
.storage-bar-segment--active {
  box-shadow: inset 0 0 0 1px var(--bg-surface);
}
.storage-bar-tooltip {
  z-index: 10;
  background: var(--bg-overlay);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}
.storage-bar-tooltip--center {
  transform: translateX(-50%);
}
.storage-bar-tooltip--end {
  transform: translateX(-100%);
}
/* Every row is the same height: the folder button and the empty slot are both 2rem square. */
.storage-category-list > li + li,
.storage-other-list > li {
  border-top: 1px solid var(--border);
}
.storage-category-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-height: 2.5rem;
  margin-inline: -0.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.375rem;
  transition: background-color 150ms;
}
.storage-other-toggle {
  width: calc(100% + 1rem);
}
.storage-other-toggle:hover {
  background: var(--bg-inset);
}
.storage-other-toggle:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 2px var(--brand-glow);
}
.storage-other-list {
  padding-left: 1.25rem;
}
.storage-dot {
  width: 0.625rem;
  height: 0.625rem;
  border-radius: 9999px;
  flex-shrink: 0;
  background: var(--dot-color);
}
/* An empty category: a ring in its colour, not a faded dot (a faded dot reads as
   a different, lighter colour). The ring keeps the family's full contrast. */
.storage-dot--empty {
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--dot-color);
}
/* Label, percent and size: one line of columns in a wide card. */
.storage-category-name {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex: 1 1 auto;
  min-width: 0;
}
.storage-category-label {
  flex: 1 1 auto;
  min-width: 0;
}
.storage-percent {
  width: 3.5rem;
  flex-shrink: 0;
  text-align: right;
  color: var(--text-muted);
}
.storage-size {
  width: 6rem;
  color: var(--text-primary);
}
.storage-icon-slot {
  width: 2rem;
  height: 2rem;
  flex-shrink: 0;
  color: var(--text-secondary);
}
.storage-chevron {
  transition: transform 150ms;
}
.storage-chevron--open {
  transform: rotate(180deg);
}
.storage-category-row--active {
  background: var(--bg-inset);
}
.storage-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  flex-shrink: 0;
  border-radius: 0.5rem;
  color: var(--text-secondary);
}
.storage-icon-btn:hover {
  color: var(--text-primary);
  background: var(--bg-overlay);
}
.storage-icon-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
/* Phone-width room (container set by StorageView): fixed percent and size
   columns would take the label's place. The label gets the full first line;
   the percent and the size share the second, the size still right-aligned
   in one column. */
@container storage-page (max-width: 30rem) {
  .storage-card {
    padding: 1rem;
  }
  .storage-category-row {
    gap: 0.5rem;
    padding-block: 0.375rem;
  }
  /* The dot marks the label line, not the middle of the two-line row
     (0.3125rem = half of the 1.25rem label line minus half the dot). */
  .storage-dot {
    align-self: flex-start;
    margin-top: 0.3125rem;
  }
  .storage-category-name {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "label label"
      "percent size";
    align-items: baseline;
    column-gap: 0.5rem;
  }
  /* A long name wraps to a second line rather than losing its end. */
  .storage-category-label {
    grid-area: label;
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .storage-percent {
    grid-area: percent;
    width: auto;
    text-align: left;
  }
  .storage-size {
    grid-area: size;
    width: auto;
  }
  .storage-other-list {
    padding-left: 0.5rem;
  }
}
</style>
