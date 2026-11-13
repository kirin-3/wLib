<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Every in-app download in one ordered list, grouped by what it needs. -->
<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {useRouter} from 'vue-router';
import {
  IconAlertTriangle, IconDownload, IconInfoCircle, IconPackage, IconPlayerPause, IconPlayerPlay, IconReload, IconTrash, IconX,
} from '@tabler/icons-vue';
import {api, type DownloadOffer, type DownloadQueueJob, type UpdateDownloadJob} from '../services/api';
import {useUpdateDownloads} from '../composables/useUpdateDownloads';
import {gamesStore} from '../composables/useGamesStore';
import DownloadRow from '../components/downloads/DownloadRow.vue';
import DownloadSpeedSummary from '../components/downloads/DownloadSpeedSummary.vue';
import DownloadQuickSettings from '../components/downloads/DownloadQuickSettings.vue';
import {
  forecastText, groupDownloads, holdText, moveOrder, pauseNeedsConfirm, removeNeedsConfirm, sectionHeading,
  stateChangeAnnouncement, type RowAction,
} from '../utils/downloadQueue';
import {formatSpeed} from '../utils/torrentDownloads';
import {confirmAction} from '../utils/confirm';
import {openInstallArchive} from '../utils/installArchive';
import {notify} from '../utils/toast';

const router = useRouter();
const downloads = useUpdateDownloads();
const {jobs, queue, seeding, runningGames, loaded} = downloads;

const offers = ref<DownloadOffer[]>([]);
const announcement = ref('');
// After a move, focus goes back to the moved row once the new order arrives.
let pendingFocus: {id: string; action: RowAction} | null = null;
const draggedId = ref<string | null>(null);
const dropIndex = ref<number | null>(null);

const ordered = computed(() => [...jobs.value].sort((a, b) => a.position - b.position));
// A detected archive that a finished download already covers shows once.
const visibleOffers = computed(() => {
  const staged = new Set(jobs.value.map((job) => job.details.archive_path).filter(Boolean));
  return offers.value.filter((offer) => !staged.has(offer.archive_path));
});
const sections = computed(() => groupDownloads(ordered.value, seeding.value, visibleOffers.value));
const waitingIds = computed(() => sections.value.find((section) => section.id === 'upnext')?.jobs.map((job) => job.job_id) ?? []);
const hasUnfinished = computed(() => jobs.value.some((job) => job.state !== 'ready'));
const anyPausable = computed(() => jobs.value.some((job) => ['running', 'queued', 'preparing', 'retry_wait', 'held'].includes(job.state)));
const anyPaused = computed(() => jobs.value.some((job) => job.state === 'paused'));
const forecast = computed(() => queue.value && hasUnfinished.value ? forecastText(queue.value.forecast) : null);
const hold = computed(() => queue.value ? holdText(queue.value.hold) : '');
const freeBytes = computed(() => queue.value?.forecast.free ?? null);
const empty = computed(() => loaded.value && !sections.value.length);

async function loadOffers() {
  try {
    const result = await api.findDownloadOffers();
    offers.value = result.success ? result.offers ?? [] : [];
  } catch (error) { console.warn('Could not read the detected archives', error); }
}

// Announce state changes politely; focus stays where it is.
let previous = new Map<string, DownloadQueueJob>();
watch(jobs, (current) => {
  const messages = current.map((job) => stateChangeAnnouncement(previous.get(job.job_id), job)).filter(Boolean);
  // A finished download may change which detected archives are new.
  const finished = current.some((job) => job.state === 'ready' && previous.get(job.job_id)?.state !== 'ready');
  previous = new Map(current.map((job) => [job.job_id, job]));
  if (messages.length) announcement.value = messages.join(' ');
  if (pendingFocus) {
    const target = pendingFocus;
    pendingFocus = null;
    void nextTick(() => focusRow(target.id, target.action));
  }
  if (finished) void loadOffers();
});

function focusRow(id: string, action: RowAction) {
  const row = document.querySelector<HTMLElement>(`[data-job-id="${CSS.escape(id)}"]`);
  if (!row) return;
  // At the top or the bottom the pressed button is gone: use the other move button.
  const other = action === 'down' ? 'up' : 'down';
  const button = row.querySelector<HTMLElement>(`[data-action="${action}"]:not(:disabled)`)
    ?? row.querySelector<HTMLElement>(`[data-action="${other}"]:not(:disabled)`)
    ?? row.querySelector<HTMLElement>('button:not(:disabled)');
  button?.focus();
}

function gameFor(gameId: number) { return gamesStore.games.value.find((game) => game.id === gameId); }

async function move(job: DownloadQueueJob, to: number, action: RowAction) {
  const order = moveOrder(ordered.value.map((item) => item.job_id), waitingIds.value, job.job_id, to);
  pendingFocus = {id: job.job_id, action};
  if (!(await downloads.reorder(order))) pendingFocus = null;
}

async function onAction(job: DownloadQueueJob, action: RowAction) {
  const index = waitingIds.value.indexOf(job.job_id);
  switch (action) {
    case 'pause':
      if (pauseNeedsConfirm(job) && !(await confirmAction({
        title: 'Pause this download?',
        message: `${job.title || 'This download'} comes from a host that cannot continue a download. Pausing deletes what was downloaded, and Resume starts it over.`,
        confirmLabel: 'Pause', danger: true,
      }))) return;
      await downloads.pause(job.job_id); return;
    case 'resume': await downloads.resume(job.job_id); return;
    case 'now': await downloads.startNow(job.job_id); return;
    case 'retry': await downloads.retry(job.job_id); return;
    case 'up': await move(job, index - 1, 'up'); return;
    case 'down': await move(job, index + 1, 'down'); return;
    case 'top': await move(job, 0, 'top'); return;
    case 'install': await downloads.install(job); return;
    case 'settings': void router.push('/settings'); return;
    case 'another': {
      const game = gameFor(job.game_id);
      if (!game) { notify('The game is no longer in the library.', 'error'); return; }
      downloads.open(game, job.job_id); return;
    }
    case 'remove':
      if (removeNeedsConfirm(job) && !(await confirmAction({
        title: 'Remove this download?',
        message: `This deletes the downloaded files of ${job.title || 'this download'}.`,
        confirmLabel: 'Remove', danger: true,
      }))) return;
      await downloads.remove(job.job_id); return;
  }
}

async function pauseAll() {
  const losing = jobs.value.filter((job) => job.state === 'running' && pauseNeedsConfirm(job));
  if (losing.length && !(await confirmAction({
    title: 'Pause all downloads?',
    message: `${losing.map((job) => job.title).join(', ')} cannot continue later. Pausing deletes what was downloaded, and Resume starts over.`,
    confirmLabel: 'Pause all', danger: true,
  }))) return;
  await downloads.pauseAll();
}

// Native drag for the "Up next" rows; the move buttons do the same by keyboard.
function onDragStart(job: DownloadQueueJob, event: DragEvent) {
  draggedId.value = job.job_id;
  if (event.dataTransfer) { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', job.job_id); }
}
function onDragOver(index: number, event: DragEvent) {
  if (!draggedId.value) return;
  event.preventDefault();
  dropIndex.value = index;
}
async function onDrop(index: number) {
  const id = draggedId.value;
  draggedId.value = null; dropIndex.value = null;
  const job = jobs.value.find((item) => item.job_id === id);
  if (!job || waitingIds.value.indexOf(job.job_id) === index) return;
  await move(job, index, 'up');
}
function onDragEnd() { draggedId.value = null; dropIndex.value = null; }

const offerRunning = (offer: DownloadOffer) => runningGames.value.includes(offer.game_id);
function installOffer(offer: DownloadOffer) {
  if (offerRunning(offer)) return;
  openInstallArchive({gameId: offer.game_id, archivePath: offer.archive_path});
}
async function dismissOffer(offer: DownloadOffer) {
  offers.value = offers.value.filter((item) => item.key !== offer.key);
  const result = await api.dismissDownloadOffer(offer.key);
  if (!result.success) notify(result.error || 'Could not dismiss the archive.', 'error');
}

const seedText = (job: UpdateDownloadJob) => {
  const ratio = (job.ratio ?? 0).toFixed(2);
  const target = job.ratio_target ? ` of ${job.ratio_target.toFixed(1)}` : '';
  return `Ratio ${ratio}${target} · ↑ ${formatSpeed(job.upload_speed ?? 0)}`;
};

const onFocus = () => { void loadOffers(); };
onMounted(() => {
  previous = new Map(jobs.value.map((job) => [job.job_id, job]));
  downloads.startPolling();
  downloads.wantHistory(true);
  void loadOffers();
  window.addEventListener('focus', onFocus);
});
onBeforeUnmount(() => {
  downloads.wantHistory(false);
  window.removeEventListener('focus', onFocus);
});
</script>

<template>
  <div class="mx-auto w-full p-8 max-w-4xl pb-12">
    <header class="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 class="ui-page-heading text-3xl font-bold mb-2 tracking-tight" style="color: var(--text-primary)">
          <IconDownload class="ui-page-heading-icon" />
          <span>Downloads</span>
        </h2>
        <p class="text-sm pl-3" style="color: var(--text-secondary); border-left: 2px solid var(--brand)">
          Game updates that wLib downloads, in the order they run.
        </p>
      </div>
      <div v-if="jobs.length" class="flex gap-2">
        <button type="button" class="tool-btn" :disabled="!anyPausable" data-testid="pause-all" @click="pauseAll"><IconPlayerPause class="w-4 h-4" />Pause all</button>
        <button type="button" class="tool-btn" :disabled="!anyPaused" data-testid="resume-all" @click="downloads.resumeAll()"><IconPlayerPlay class="w-4 h-4" />Resume all</button>
      </div>
    </header>

    <p class="sr-only" role="status" aria-live="polite" data-testid="downloads-live">{{ announcement }}</p>

    <div class="space-y-4">
      <div v-if="hold" class="banner" role="status" data-testid="hold-banner">
        <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" />
        <div class="flex-1 min-w-0">
          <p>{{ hold }}</p>
          <p v-if="queue?.hold_keeps_running.length" class="text-xs mt-1" style="color: var(--text-muted)">Downloads that cannot continue later keep running.</p>
        </div>
        <button type="button" class="tool-btn" data-testid="resume-now" @click="downloads.resumeNow()">Resume now</button>
      </div>

      <template v-if="queue && jobs.length">
        <DownloadSpeedSummary :queue="queue" :jobs="jobs" />
        <p v-if="forecast" class="text-sm flex items-start gap-2" :style="{ color: forecast.warning ? 'var(--danger-text)' : 'var(--text-secondary)' }" data-testid="download-forecast">
          <IconAlertTriangle v-if="forecast.warning" class="w-4 h-4 shrink-0 mt-0.5" />{{ forecast.text }}
        </p>
      </template>
      <DownloadQuickSettings v-if="queue" :settings="queue.settings" @saved="downloads.refresh()" />

      <div v-if="empty" class="empty" data-testid="downloads-empty">
        <IconDownload class="w-10 h-10 mx-auto mb-3" style="color: var(--text-muted)" />
        <p class="font-medium" style="color: var(--text-primary)">No downloads</p>
        <p class="text-sm mt-1" style="color: var(--text-secondary)">Choose "Download update" on a game in Updates to start one.</p>
        <button type="button" class="tool-btn mt-4" @click="router.push('/updates')"><IconReload class="w-4 h-4" />Open Updates</button>
      </div>

      <section v-for="section in sections" :key="section.id" :aria-labelledby="`section-${section.id}`" :data-testid="`section-${section.id}`">
        <h3 :id="`section-${section.id}`" class="text-sm font-semibold mb-2 uppercase tracking-wide" style="color: var(--text-secondary)">
          {{ sectionHeading(section, queue ?? undefined) }}
        </h3>
        <ul class="space-y-2" @dragleave.self="dropIndex = null">
          <DownloadRow
            v-for="(job, index) in section.jobs"
            :key="job.job_id"
            :class="{ dragging: draggedId === job.job_id, 'drop-before': section.id === 'upnext' && dropIndex === index && draggedId !== job.job_id }"
            :job="job"
            :free-bytes="freeBytes"
            :game-running="runningGames.includes(job.game_id)"
            :can-move-up="section.id === 'upnext' && index > 0"
            :can-move-down="section.id === 'upnext' && index < section.jobs.length - 1"
            :draggable="section.id === 'upnext' && section.jobs.length > 1"
            @action="onAction(job, $event)"
            @seed="(seed, remember) => downloads.answerSeed(job.job_id, seed, remember)"
            @dragstart="onDragStart(job, $event)"
            @dragend="onDragEnd"
            @dragover="section.id === 'upnext' && onDragOver(index, $event)"
            @drop.prevent="section.id === 'upnext' && onDrop(index)"
          />

          <li v-for="offer in section.offers" :key="offer.key" class="plain-row" data-testid="download-offer">
            <div class="min-w-0">
              <p class="font-semibold truncate" style="color: var(--text-primary)">{{ offer.game_title }}</p>
              <p class="text-xs mt-0.5 truncate" style="color: var(--text-muted)" :title="offer.archive_path">
                Found in your Downloads folder · version {{ offer.archive_version || offer.latest_version }}
              </p>
              <p v-if="offerRunning(offer)" class="text-xs mt-1" style="color: var(--text-muted)">Close the game to install the update.</p>
            </div>
            <div class="flex flex-wrap gap-2 mt-3">
              <button type="button" class="tool-btn primary" :disabled="offerRunning(offer)" :title="offerRunning(offer) ? 'Close the game to install the update' : 'Install update'" @click="installOffer(offer)"><IconPackage class="w-4 h-4" />Install update</button>
              <button type="button" class="tool-btn" title="Hide this archive; the file stays" @click="dismissOffer(offer)"><IconX class="w-4 h-4" />Dismiss</button>
            </div>
          </li>

          <li v-for="torrent in section.seeding" :key="torrent.job_id" class="plain-row" data-testid="seeding-row">
            <div class="min-w-0">
              <p class="font-semibold truncate" style="color: var(--text-primary)">{{ torrent.title || 'Torrent' }}</p>
              <p class="text-xs mt-0.5 font-mono" style="color: var(--text-muted)">{{ seedText(torrent) }}</p>
            </div>
            <div class="flex gap-2 mt-3">
              <button type="button" class="tool-btn" @click="downloads.stopSeeding(torrent.job_id)"><IconTrash class="w-4 h-4" />Stop seeding</button>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.9rem;
  border-radius: 0.5rem;
  font-size: 0.85rem;
  font-weight: 500;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
}
.tool-btn.primary { background: var(--brand); border-color: var(--brand); color: var(--on-brand); }
.tool-btn:hover:not(:disabled) { background: var(--border-hover); }
.tool-btn.primary:hover:not(:disabled) { background: var(--brand); filter: brightness(1.1); }
.tool-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.tool-btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.banner {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  padding: 0.75rem 1rem;
  border-radius: 0.75rem;
  font-size: 0.875rem;
  color: var(--warning-text);
  background: var(--tag-yellow-bg);
  border: 1px solid var(--tag-yellow-border);
}
.empty {
  text-align: center;
  padding: 3rem 1rem;
  border-radius: 0.75rem;
  border: 1px dashed var(--border-hover);
}
.plain-row {
  list-style: none;
  padding: 0.85rem 1rem;
  border-radius: 0.75rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
</style>
