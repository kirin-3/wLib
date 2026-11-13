// SPDX-License-Identifier: GPL-3.0-or-later
import {ref} from 'vue';
import {api, type GameRecord, type UpdateDownloadJob} from '../services/api';
import {openInstallArchive} from '../utils/installArchive';
import {confirmChoice} from '../utils/confirm';
import {isSeeding, isTorrentJob, needsSeedAnswer, seedPromptText, torrentInstallSource, TORRENT_RUNNING_STAGES} from '../utils/torrentDownloads';
import {notify} from '../utils/toast';
import {pollDelay, windowActive} from '../utils/polling';

const jobs = ref<UpdateDownloadJob[]>([]);
const runningGames = ref<number[]>([]);
const selectedGame = ref<GameRecord | null>(null);
const handled = new Set<string>();
const installers = new Map<string, () => Promise<void>>();
let timer: ReturnType<typeof setTimeout> | undefined;
let enabled = false;
let polling = false;
let installDialogOpen = false;
// The downloaded update the install dialog was opened for, until it is installed.
let offered: UpdateDownloadJob | null = null;
// A game has a download "in progress" until it finishes; a paused torrent still counts.
const active = (job: UpdateDownloadJob) =>
  ['resolving','needs_user','downloading','verifying'].includes(job.stage) || (isTorrentJob(job) && TORRENT_RUNNING_STAGES.includes(job.stage));
// Poll quickly only while bytes are moving or a step is pending.
const fast = (job: UpdateDownloadJob) => ['resolving','needs_user','downloading','verifying','fetching'].includes(job.stage);
// Poll quickly while bytes move, often while a download is active, and
// rarely when there is none: an idle poll costs CPU for nothing.
export const downloadPollMs = (list: UpdateDownloadJob[]) =>
  list.some(fast) ? 500 : list.some(active) ? 2000 : 10_000;
// Torrent jobs the Updates view lists: running, failed with a retry, or seeding.
const visibleTorrent = (job: UpdateDownloadJob) =>
  isTorrentJob(job) && (active(job) || job.stage === 'failed' || isSeeding(job) || needsSeedAnswer(job));
const promptedSeed = new Set<string>();

const installPath = (job: UpdateDownloadJob) => torrentInstallSource(job)?.archivePath ?? job.archive_path;

// Settles the seeding question (torrents only), then opens the install dialog or leaves a way back to it.
async function offerInstall(job: UpdateDownloadJob, running: number[]) {
  if (needsSeedAnswer(job) && !promptedSeed.has(job.job_id)) {
    promptedSeed.add(job.job_id);
    const answer = await confirmChoice(seedPromptText(job));
    const result = await api.answerSeedPrompt(job.job_id, answer.confirmed, answer.checked);
    if (!result.success) notify(result.error || 'Could not save the seeding answer.', 'error');
    refresh();
  }
  const install = async () => {
    if ((await api.getRunningGames()).includes(job.game_id)) {
      notify('Close the game before installing its update.', 'error');
      return;
    }
    if (installDialogOpen) {
      notify('Download complete. Open the update when the current install dialog is closed.', 'info', '', {label:'Install update',run:()=>{void install();}});
      return;
    }
    installDialogOpen = true;
    offered = job;
    openInstallArchive({gameId:job.game_id,archivePath:installPath(job)});
  };
  installers.set(job.job_id, install);
  if (running.includes(job.game_id)) notify('Download complete. Close the game to install the update.', 'info', '', {label:'Install update',run:()=>{void install();}});
  else void install();
}

async function poll() {
  if (!enabled || polling) return;
  polling = true;
  try {
    const [status, running] = await Promise.all([api.getUpdateDownloadStatus(),api.getRunningGames()]);
    if (!enabled) return;
    runningGames.value = running;
    if (status.success) jobs.value = status.jobs || [];
    for (const job of jobs.value) {
      if (job.stage !== 'done' || handled.has(job.job_id)) continue;
      if (isTorrentJob(job)) {
        // Already installed in an earlier session, or nothing left to install from.
        if (job.installed || (!torrentInstallSource(job) && !needsSeedAnswer(job))) continue;
      } else if (!job.archive_path) continue;
      handled.add(job.job_id);
      void offerInstall(job, running);
    }
  } catch (error) { console.warn('Could not read download status',error); }
  finally {
    polling = false;
    if (enabled) timer = setTimeout(poll,pollDelay(downloadPollMs(jobs.value),windowActive()));
  }
}
function refresh() { if (timer) clearTimeout(timer); timer = undefined; void poll(); }
export function useUpdateDownloads() {
  return {
    jobs, runningGames, selectedGame,
    setInstallDialogOpen(value: boolean) {
      installDialogOpen = value;
      // Closed without installing: the archive is still in the downloads
      // folder, so keep a way back to it.
      if (!value && offered) {
        const job = offered;
        offered = null;
        const install = installers.get(job.job_id);
        if (install) notify('The downloaded update was not installed.', 'info', '', {label:'Install update',run:()=>{void install();}});
      }
    },
    markInstalled(archivePath: string) {
      if (offered && installPath(offered) === archivePath) {
        installers.delete(offered.job_id);
        offered = null;
      }
    },
    open(game: GameRecord) { selectedGame.value = game; },
    close() { selectedGame.value = null; },
    jobFor(gameId: number) { return jobs.value.find(job=>job.game_id===gameId && active(job)); },
    torrentJobs() { return jobs.value.filter(visibleTorrent); },
    startPolling() { if (!enabled) { enabled = true; void poll(); } },
    refresh,
    stopPolling() { enabled = false; if (timer) clearTimeout(timer); timer = undefined; },
  };
}
