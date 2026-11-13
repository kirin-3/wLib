// SPDX-License-Identifier: GPL-3.0-or-later
// The one app-wide poller for in-app downloads: the queue, its summary, the
// seeding torrents, and the commands. The Downloads page, the sidebar entry,
// the Updates view and the game details read it.
import {computed, ref} from 'vue';
import {useRouter} from 'vue-router';
import {
  api,
  type DownloadQueueJob,
  type DownloadQueueSummary,
  type GameRecord,
  type UpdateDownloadJob,
} from '../services/api';
import {openInstallArchive} from '../utils/installArchive';
import {confirmChoice} from '../utils/confirm';
import {
  awaitsSeedAnswer,
  downloadPollMs,
  installSource,
  jobForGame,
  runQueueCommand,
  shouldAutoOpen,
  sidebarBadge,
} from '../utils/downloadQueue';
import {seedPromptText} from '../utils/torrentDownloads';
import {notify} from '../utils/toast';
import {pollDelay, windowActive} from '../utils/polling';

const jobs = ref<DownloadQueueJob[]>([]);
const queue = ref<DownloadQueueSummary | null>(null);
const seeding = ref<UpdateDownloadJob[]>([]);
const runningGames = ref<number[]>([]);
const loaded = ref(false);
/** The game the build picker is open for, and the failed download it replaces. */
const selectedGame = ref<GameRecord | null>(null);
const replacing = ref<string | null>(null);
const handled = new Set<string>();
let timer: ReturnType<typeof setTimeout> | undefined;
let enabled = false;
let polling = false;
let installDialogOpen = false;
// The finished download the install dialog was opened for, until it is installed.
let offered: DownloadQueueJob | null = null;
// Pages that show the speed chart ask for the history while they are open.
let historyWanted = 0;
let openDownloadsPage: (() => void) | null = null;

const badge = computed(() => sidebarBadge(jobs.value));

const reportError = (message: string) => { notify(message, 'error'); };

function toastActions(job: DownloadQueueJob) {
  return {label: 'Install update', run: () => { void install(job); }};
}

/** Opens the install dialog for a finished download, or says why it cannot. */
async function install(job: DownloadQueueJob): Promise<void> {
  const source = installSource(job);
  if (!source) {
    notify('The downloaded files are gone. Remove the download and start it again.', 'error');
    return;
  }
  if ((await api.getRunningGames()).includes(job.game_id)) {
    notify('Close the game before installing its update.', 'error');
    return;
  }
  if (installDialogOpen) {
    notify('Finish or close the open install dialog first.', 'info', '', toastActions(job));
    return;
  }
  installDialogOpen = true;
  offered = job;
  openInstallArchive({gameId: job.game_id, archivePath: source.archivePath});
}

/** Settles the seeding question in a dialog, then opens the install dialog. */
async function askSeedThenInstall(job: DownloadQueueJob): Promise<void> {
  const answer = await confirmChoice(seedPromptText({
    job_id: job.job_id, game_id: job.game_id, stage: 'done', bytes: job.bytes_done, total: job.bytes_total,
    speed: 0, error: '', archive_path: job.details.archive_path ?? '', title: job.title,
    ratio_target: job.details.ratio_target ?? 0, payload_kind: job.details.payload_kind ?? '',
  }));
  const result = await api.answerSeedPrompt(job.job_id, answer.confirmed, answer.checked);
  if (!result.success) notify(result.error || 'Could not save the seeding answer.', 'error');
  await install(job);
}

/** A download that just finished: open the dialog when it is the only one, else offer it. */
function offerFinished(job: DownloadQueueJob, running: number[]): void {
  if (shouldAutoOpen(job, jobs.value, running, installDialogOpen)) {
    if (awaitsSeedAnswer(job)) void askSeedThenInstall(job);
    else void install(job);
    return;
  }
  const open = {label: 'Open Downloads', run: () => { openDownloadsPage?.(); }};
  if (awaitsSeedAnswer(job)) {
    notify(`${job.title || 'A torrent'} finished. Choose whether to keep seeding on the Downloads page.`, 'info', '', open);
  } else if (running.includes(job.game_id)) {
    notify('Download complete. Close the game to install the update.', 'info', '', toastActions(job));
  } else {
    notify(`${job.title || 'A download'} is ready to install.`, 'info', '', toastActions(job));
  }
}

async function poll() {
  if (!enabled || polling) return;
  polling = true;
  try {
    const [status, running] = await Promise.all([api.getDownloadQueue(historyWanted > 0), api.getRunningGames()]);
    if (!enabled) return;
    runningGames.value = running;
    if (status.success) {
      jobs.value = status.jobs ?? [];
      queue.value = status.queue ?? null;
      seeding.value = status.seeding ?? [];
      const first = !loaded.value;
      loaded.value = true;
      for (const job of jobs.value) {
        if (job.state !== 'ready' || handled.has(job.job_id)) continue;
        handled.add(job.job_id);
        // Finished in an earlier session: the page lists it; no dialog now.
        if (!first) offerFinished(job, running);
      }
    }
  } catch (error) { console.warn('Could not read the downloads', error); }
  finally {
    polling = false;
    if (enabled) timer = setTimeout(poll, pollDelay(downloadPollMs(jobs.value), windowActive()));
  }
}

function refresh() { if (timer) clearTimeout(timer); timer = undefined; void poll(); }

const run = (command: () => Promise<{success?: boolean | undefined; error?: string | undefined}>) =>
  runQueueCommand(command, refresh, reportError);

export function useUpdateDownloads() {
  const router = useRouter();
  openDownloadsPage = () => { void router.push('/downloads'); };
  return {
    jobs, queue, seeding, runningGames, selectedGame, replacing, loaded, badge,
    setInstallDialogOpen(value: boolean) {
      installDialogOpen = value;
      // Closed without installing: it stays under "Ready to install".
      if (!value && offered) {
        const job = offered;
        offered = null;
        if (jobs.value.some((item) => item.job_id === job.job_id)) {
          notify('The downloaded update was not installed. It stays in Downloads.', 'info', '', toastActions(job));
        }
      }
    },
    markInstalled(archivePath: string) {
      if (offered && installSource(offered)?.archivePath === archivePath) offered = null;
      refresh();
    },
    /** Opens the build picker; `replaces` puts the new download in a failed one's place. */
    open(game: GameRecord, replaces: string | null = null) { replacing.value = replaces; selectedGame.value = game; },
    close() { selectedGame.value = null; replacing.value = null; },
    jobFor(gameId: number) { return jobForGame(jobs.value, gameId); },
    install,
    startPolling() { if (!enabled) { enabled = true; void poll(); } },
    refresh,
    stopPolling() { enabled = false; if (timer) clearTimeout(timer); timer = undefined; },
    /** The Downloads page shows the chart: ask for the speed history while it is open. */
    wantHistory(on: boolean) { historyWanted = Math.max(0, historyWanted + (on ? 1 : -1)); refresh(); },
    reorder: (ids: string[]) => run(() => api.reorderDownloads(ids)),
    startNow: (id: string) => run(() => api.startDownloadNow(id)),
    pause: (id: string) => run(() => api.pauseDownload(id)),
    resume: (id: string) => run(() => api.resumeDownload(id)),
    pauseAll: () => run(() => api.pauseAllDownloads()),
    resumeAll: () => run(() => api.resumeAllDownloads()),
    retry: (id: string) => run(() => api.retryDownload(id)),
    remove: (id: string) => run(() => api.removeDownload(id)),
    resumeNow: () => run(() => api.resumeDownloadsNow()),
    stopSeeding: (id: string) => run(() => api.stopSeeding(id)),
    answerSeed: (id: string, seed: boolean, remember: boolean) => run(() => api.answerSeedPrompt(id, seed, remember)),
  };
}
