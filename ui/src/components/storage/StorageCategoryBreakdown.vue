<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { IconChartPie, IconFolderOpen } from "@tabler/icons-vue";
import type { StorageCategoryReport } from "../../services/api";
import { formatStorageBytes, sortedStorageCategories } from "../../utils/storage";

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

const BAR_COLORS: Record<string, string> = {
  games: "var(--brand)",
  download_archives: "#3b82f6",
  torrent_data: "#06b6d4",
  proton_builds: "#8b5cf6",
  wine_prefixes: "#a855f7",
  shared_wine_prefix: "#ec4899",
  save_backups: "#10b981",
  web_player: "#14b8a6",
  sandboxes: "#f59e0b",
  removed_game_data: "#ef4444",
  cover_cache: "#6366f1",
  other_caches: "#64748b",
  catalog: "#0ea5e9",
  logs_and_backups: "#94a3b8",
};

const sortedCategories = computed(() => sortedStorageCategories(props.categories));

const nonZeroCategories = computed(() =>
  sortedCategories.value.filter((cat) => cat.bytes > 0),
);

const barEl = ref<HTMLElement | null>(null);
const hoveredId = ref("");
// Tooltip anchor: the segment centre in pixels from the bar's left edge.
const tooltipX = ref(0);
const tooltipAlign = ref<"start" | "center" | "end">("center");

const hoveredCategory = computed(
  () => nonZeroCategories.value.find((cat) => cat.id === hoveredId.value) ?? null,
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

const barColor = (id: string): string => BAR_COLORS[id] ?? "var(--brand)";
</script>

<template>
  <section class="storage-card rounded-xl p-6" aria-labelledby="storage-categories-heading">
    <div class="flex flex-wrap items-baseline justify-between gap-2 mb-4">
      <h3 id="storage-categories-heading" class="ui-section-heading text-lg font-semibold">
        <IconChartPie class="ui-section-icon" />
        <span>Where the space goes</span>
      </h3>
      <span class="text-sm font-semibold tabular-nums" style="color: var(--text-primary)">
        Total: {{ formatStorageBytes(totalBytes) }}
      </span>
    </div>

    <div v-if="totalBytes > 0" class="relative mb-5">
      <div
        v-if="hoveredCategory"
        class="storage-bar-tooltip pointer-events-none absolute bottom-full mb-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium tabular-nums"
        :class="`storage-bar-tooltip--${tooltipAlign}`"
        :style="{ left: `${tooltipX}px` }"
        role="tooltip"
      >
        <span
          class="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
          :style="{ backgroundColor: barColor(hoveredCategory.id) }"
        ></span>
        <span style="color: var(--text-primary)">{{ hoveredCategory.label }}</span>
        <span style="color: var(--text-secondary)">
          · {{ formatStorageBytes(hoveredCategory.bytes) }} ({{ hoveredCategory.percent.toFixed(1) }}%)
        </span>
      </div>
      <div
        ref="barEl"
        class="storage-bar flex h-3 w-full overflow-hidden rounded-full"
        aria-hidden="true"
        @mouseleave="clearSegment"
      >
        <div
          v-for="cat in nonZeroCategories"
          :key="cat.id"
          class="storage-bar-segment h-full transition-all duration-300"
          :class="{ 'storage-bar-segment--dim': hoveredId && hoveredId !== cat.id }"
          :style="{
            width: `${Math.max(cat.percent, 0.8)}%`,
            backgroundColor: barColor(cat.id),
          }"
          @mouseenter="showSegment($event, cat.id)"
        ></div>
      </div>
    </div>

    <ul class="divide-y" style="border-color: var(--border)">
      <li
        v-for="cat in sortedCategories"
        :key="cat.id"
        class="storage-category-row -mx-2 px-2 py-2.5 flex items-center gap-3 text-sm"
        :class="{ 'storage-category-row--active': hoveredId === cat.id }"
        style="border-color: var(--border)"
      >
        <span
          class="w-2.5 h-2.5 rounded-full shrink-0"
          :style="{ backgroundColor: barColor(cat.id), opacity: cat.bytes > 0 ? 1 : 0.35 }"
          aria-hidden="true"
        ></span>
        <span class="min-w-0 flex-1 truncate font-medium" style="color: var(--text-primary)">
          {{ cat.label }}
        </span>
        <span class="tabular-nums text-xs w-14 text-right shrink-0" style="color: var(--text-muted)">
          {{ cat.percent.toFixed(1) }}%
        </span>
        <span class="tabular-nums font-semibold w-24 text-right shrink-0" style="color: var(--text-primary)">
          {{ formatStorageBytes(cat.bytes) }}
        </span>
        <button
          v-if="OPENABLE_CATEGORIES.has(cat.id) && cat.bytes > 0"
          type="button"
          class="storage-icon-btn p-1.5 rounded-lg shrink-0"
          :aria-label="`Open ${cat.label} folder`"
          :title="`Open ${cat.label} folder`"
          @click="emit('open-location', cat.id)"
        >
          <IconFolderOpen class="w-4 h-4" />
        </button>
        <span v-else class="w-7 shrink-0" aria-hidden="true"></span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.storage-bar {
  background: var(--bg-inset);
}
.storage-bar-segment {
  cursor: default;
}
.storage-bar-segment--dim {
  opacity: 0.45;
}
.storage-bar-tooltip {
  z-index: 10;
  background: var(--bg-overlay);
  border: 1px solid var(--border);
  box-shadow: 0 4px 14px rgb(0 0 0 / 0.25);
}
.storage-bar-tooltip--center {
  transform: translateX(-50%);
}
.storage-bar-tooltip--end {
  transform: translateX(-100%);
}
.storage-category-row {
  border-radius: 0.375rem;
  transition: background-color 150ms;
}
.storage-category-row--active {
  background: var(--bg-inset);
}
.storage-icon-btn {
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
</style>
