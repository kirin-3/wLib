<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconChartPie, IconFolderOpen } from "@tabler/icons-vue";
import type { StorageCategoryReport } from "../../services/api";
import { formatStorageBytes } from "../../utils/storage";

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

const nonZeroCategories = computed(() =>
  props.categories.filter((cat) => cat.bytes > 0),
);

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

    <div
      v-if="totalBytes > 0"
      class="storage-bar flex h-3 w-full overflow-hidden rounded-full mb-5"
      aria-hidden="true"
    >
      <div
        v-for="cat in nonZeroCategories"
        :key="cat.id"
        class="h-full transition-all duration-300"
        :style="{
          width: `${Math.max(cat.percent, 0.8)}%`,
          backgroundColor: barColor(cat.id),
        }"
        :title="`${cat.label}: ${formatStorageBytes(cat.bytes)} (${cat.percent}%)`"
      ></div>
    </div>

    <ul class="divide-y" style="border-color: var(--border)">
      <li
        v-for="cat in categories"
        :key="cat.id"
        class="py-2.5 flex items-center gap-3 text-sm"
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
