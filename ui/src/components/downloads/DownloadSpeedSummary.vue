<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Combined speed now, the last five minutes as a chart, the peak and the time left. -->
<script setup lang="ts">
import {computed} from 'vue';
import type {DownloadQueueJob, DownloadQueueSummary} from '../../services/api';
import {chartPoints, chartSummary, formatSeconds, totalTimeLeft} from '../../utils/downloadQueue';
import {formatSpeed} from '../../utils/torrentDownloads';

const props = defineProps<{ queue: DownloadQueueSummary; jobs: DownloadQueueJob[] }>();

const WIDTH = 300;
const HEIGHT = 48;
const history = computed(() => props.queue.history ?? []);
const running = computed(() => props.jobs.some((job) => job.state === 'running'));
const points = computed(() => chartPoints(history.value, WIDTH, HEIGHT));
const area = computed(() => points.value ? `0,${HEIGHT} ${points.value} ${WIDTH},${HEIGHT}` : '');
const summary = computed(() => chartSummary(history.value));
const timeLeft = computed(() => {
  const seconds = totalTimeLeft(props.jobs, props.queue.speed);
  return seconds === null ? '' : formatSeconds(seconds);
});
const limit = computed(() => props.queue.settings.download_limit_kib);
</script>

<template>
  <section class="summary" aria-labelledby="speed-heading" data-testid="speed-summary">
    <div class="flex flex-wrap items-baseline gap-x-6 gap-y-1">
      <h3 id="speed-heading" class="sr-only">Download speed</h3>
      <p v-if="!running && queue.speed <= 0" class="text-sm" style="color: var(--text-secondary)" data-testid="speed-idle">Nothing is downloading.</p>
      <template v-else>
        <p class="text-2xl font-bold font-mono" style="color: var(--text-primary)">{{ formatSpeed(queue.speed) }}</p>
        <p class="text-xs" style="color: var(--text-muted)">Peak {{ formatSpeed(queue.peak) }}</p>
        <p v-if="timeLeft" class="text-xs" style="color: var(--text-muted)">{{ timeLeft }} left for all</p>
      </template>
      <p v-if="limit > 0" class="text-xs ml-auto" style="color: var(--text-muted)">Limit {{ formatSpeed(limit * 1024) }}</p>
    </div>
    <figure v-if="points" class="mt-2">
      <svg :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" preserveAspectRatio="none" class="chart" role="img" :aria-label="summary">
        <polygon :points="area" class="chart-area" />
        <polyline :points="points" class="chart-line" vector-effect="non-scaling-stroke" />
      </svg>
      <figcaption class="sr-only">{{ summary }}</figcaption>
    </figure>
  </section>
</template>

<style scoped>
.summary {
  padding: 0.85rem 1rem;
  border-radius: 0.75rem;
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.chart { width: 100%; height: 3rem; display: block; }
.chart-line { fill: none; stroke: var(--brand); stroke-width: 1.5; }
.chart-area { fill: var(--brand-glow); stroke: none; }
</style>
