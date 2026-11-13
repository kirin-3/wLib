<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- One download on the Downloads page: its state, progress, cause and actions. -->
<script setup lang="ts">
import {computed, ref} from 'vue';
import {
  IconAlertTriangle, IconArrowBarToUp, IconArrowDown, IconArrowUp, IconGripVertical, IconPackage,
  IconPlayerPause, IconPlayerPlay, IconRefresh, IconSettings, IconSwitchHorizontal, IconTrash, IconBolt,
} from '@tabler/icons-vue';
import type {DownloadQueueJob} from '../../services/api';
import {
  FAILURE_ACTION_LABELS, awaitsSeedAnswer, failureInfo, percentOf, progressText, rowActions,
  sourceLabel, stateText, type RowAction,
} from '../../utils/downloadQueue';

const props = defineProps<{
  job: DownloadQueueJob;
  /** Free bytes on the downloads drive, for the disk failure sentence. */
  freeBytes: number | null;
  /** The game runs, so "Install update" waits. */
  gameRunning: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  /** Rows in "Up next" can be dragged. */
  draggable: boolean;
}>();
const emit = defineEmits<{
  action: [action: RowAction];
  seed: [seed: boolean, remember: boolean];
  dragstart: [event: DragEvent];
  dragend: [];
}>();

const rememberSeed = ref(false);
const percent = computed(() => percentOf(props.job));
const failure = computed(() => props.job.state === 'attention' ? failureInfo(props.job, props.freeBytes) : null);
const seedQuestion = computed(() => awaitsSeedAnswer(props.job));
const actions = computed(() => rowActions(props.job).filter((action) => {
  if (action === 'up') return props.canMoveUp;
  if (action === 'down') return props.canMoveDown;
  if (action === 'top') return props.canMoveUp;
  return true;
}));
const progress = computed(() => progressText(props.job));
const sourceLine = computed(() => {
  const parts = [sourceLabel(props.job)];
  if (props.job.source.label) parts.push(props.job.source.label);
  if (props.job.source.part) parts.push(`Part ${props.job.source.part}`);
  return parts.join(' · ');
});
const indeterminate = computed(() => props.job.state === 'running' && (percent.value === null || !!props.job.details.hint));
const barShown = computed(() => ['running', 'paused', 'queued', 'held', 'retry_wait'].includes(props.job.state) && (props.job.bytes_done > 0 || props.job.state === 'running'));

const LABELS: Record<RowAction, string> = {
  ...FAILURE_ACTION_LABELS,
  pause: 'Pause', resume: 'Resume', now: 'Download now', up: 'Move up', down: 'Move down', top: 'To the top',
  install: 'Install update',
};
const ICONS = {
  pause: IconPlayerPause, resume: IconPlayerPlay, now: IconBolt, up: IconArrowUp, down: IconArrowDown,
  top: IconArrowBarToUp, remove: IconTrash, install: IconPackage, retry: IconRefresh,
  another: IconSwitchHorizontal, settings: IconSettings,
} satisfies Record<RowAction, unknown>;
// The move buttons show only their icon; the label is for assistive technology.
const iconOnly = (action: RowAction) => action === 'up' || action === 'down' || action === 'top';
const label = (action: RowAction) => `${LABELS[action]}${iconOnly(action) ? `: ${props.job.title}` : ''}`;
// One filled button per row: the step that moves the download forward.
const isPrimary = (action: RowAction) => action === 'install' || action === 'resume' || (action === 'now' && props.job.state !== 'paused');
const disabled = (action: RowAction) => action === 'install' && props.gameRunning;
</script>

<template>
  <li
    class="row"
    :class="{ attention: job.state === 'attention' || seedQuestion }"
    :data-job-id="job.job_id"
    data-testid="download-row"
    :draggable="draggable"
    @dragstart="emit('dragstart', $event)"
    @dragend="emit('dragend')"
  >
    <div class="flex items-start gap-3">
      <IconGripVertical v-if="draggable" class="grip w-4 h-4 mt-1 shrink-0" aria-hidden="true" />
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 flex-wrap min-w-0">
          <span class="font-semibold truncate" style="color: var(--text-primary)" :title="job.title">{{ job.title || 'Unknown game' }}</span>
          <span v-if="job.version" class="chip font-mono shrink-0">{{ job.version }}</span>
        </div>
        <p class="text-xs mt-0.5 truncate" style="color: var(--text-muted)">{{ sourceLine }}</p>
        <p class="text-sm mt-1 flex items-center gap-1.5" :style="{ color: job.state === 'attention' ? 'var(--danger-text)' : 'var(--text-secondary)' }" data-testid="download-state">
          <IconAlertTriangle v-if="job.state === 'attention'" class="w-4 h-4 shrink-0" aria-hidden="true" />
          {{ stateText(job) }}
        </p>
        <p v-if="failure" class="text-sm mt-1" style="color: var(--text-secondary)" data-testid="download-failure">{{ failure.sentence }}</p>
        <p v-if="job.state === 'paused' && !job.can_continue && job.bytes_done === 0 && job.kind === 'host'" class="text-xs mt-1" style="color: var(--text-muted)">This host cannot continue a download. Resume starts it over.</p>
        <div v-if="barShown" class="bar mt-2" role="progressbar" :aria-label="`${job.title} progress`" :aria-valuenow="percent ?? undefined" aria-valuemin="0" aria-valuemax="100">
          <div class="bar-fill" :class="{ indeterminate, idle: job.state !== 'running' }" :style="!indeterminate && percent !== null ? { width: percent + '%' } : undefined"></div>
        </div>
        <p v-if="progress" class="text-xs mt-1.5 font-mono" style="color: var(--text-muted)">{{ progress }}</p>
        <p v-if="job.state === 'ready' && gameRunning && !seedQuestion" class="text-xs mt-1.5" style="color: var(--text-muted)">Close the game to install the update.</p>

        <div v-if="seedQuestion" class="mt-2 space-y-2" data-testid="seed-question">
          <p class="text-sm" style="color: var(--text-primary)">Keep seeding this torrent{{ job.details.ratio_target ? ` until ratio ${job.details.ratio_target.toFixed(1)}` : '' }}?</p>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button" class="row-btn" @click="emit('seed', true, rememberSeed)">Seed</button>
            <button type="button" class="row-btn" @click="emit('seed', false, rememberSeed)">Don't seed</button>
            <label class="flex items-center gap-1.5 text-xs" style="color: var(--text-secondary)">
              <input v-model="rememberSeed" type="checkbox" />Don't ask again
            </label>
          </div>
        </div>
      </div>
    </div>
    <div v-if="actions.length" class="actions mt-3 flex flex-wrap items-center gap-2">
      <button
        v-for="action in actions"
        :key="action"
        type="button"
        class="row-btn"
        :class="{ 'icon-only': iconOnly(action), primary: isPrimary(action) }"
        :data-action="action"
        :aria-label="label(action)"
        :title="disabled(action) ? 'Close the game to install the update' : LABELS[action]"
        :disabled="disabled(action)"
        @click="emit('action', action)"
      >
        <component :is="ICONS[action]" class="w-4 h-4" aria-hidden="true" />
        <span v-if="!iconOnly(action)">{{ LABELS[action] }}</span>
      </button>
    </div>
  </li>
</template>

<style scoped>
.row {
  list-style: none;
  padding: 0.85rem 1rem;
  border-radius: 0.75rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
.row.attention { border-color: var(--danger-border); }
.row[draggable="true"] { cursor: grab; }
.row.dragging { opacity: 0.5; }
.row.drop-before { box-shadow: 0 -3px 0 0 var(--brand); }
.grip { color: var(--text-muted); }
.chip {
  padding: 0.05rem 0.45rem;
  border-radius: 0.375rem;
  font-size: 0.7rem;
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.bar {
  position: relative;
  height: 0.35rem;
  border-radius: 9999px;
  background: var(--bg-overlay);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: inherit;
  background: var(--brand);
  transition: width var(--motion-base) ease;
}
.bar-fill.idle { background: var(--text-muted); }
.bar-fill.indeterminate { width: 35%; animation: slide 1.2s ease-in-out infinite; }
@keyframes slide {
  from { transform: translateX(-100%); }
  to { transform: translateX(290%); }
}
@media (prefers-reduced-motion: reduce) {
  .bar-fill.indeterminate { animation: none; width: 100%; opacity: 0.5; }
}
.row-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.35rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.8rem;
  font-weight: 500;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
}
.row-btn.icon-only { padding: 0.35rem 0.45rem; }
.row-btn.primary { background: var(--brand); border-color: var(--brand); color: var(--on-brand); }
.row-btn:hover:not(:disabled) { background: var(--border-hover); }
.row-btn.primary:hover:not(:disabled) { filter: brightness(1.1); background: var(--brand); }
.row-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.row-btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
</style>
