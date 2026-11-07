// SPDX-License-Identifier: GPL-3.0-or-later
import {ref} from 'vue';
import {api, type GameRecord, type UpdateDownloadJob} from '../services/api';
import {openInstallArchive} from '../utils/installArchive';
import {notify} from '../utils/toast';

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
const active = (job: UpdateDownloadJob) => ['resolving','needs_user','downloading','verifying'].includes(job.stage);

async function poll() {
  if (!enabled || polling) return;
  polling = true;
  try {
    const [status, running] = await Promise.all([api.getUpdateDownloadStatus(),api.getRunningGames()]);
    if (!enabled) return;
    runningGames.value = running;
    if (status.success) jobs.value = status.jobs || [];
    for (const job of jobs.value) {
      if (job.stage !== 'done' || !job.archive_path || handled.has(job.job_id)) continue;
      handled.add(job.job_id);
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
        openInstallArchive({gameId:job.game_id,archivePath:job.archive_path});
      };
      installers.set(job.job_id, install);
      if (running.includes(job.game_id)) notify('Download complete. Close the game to install the update.', 'info', '', {label:'Install update',run:()=>{void install();}});
      else void install();
    }
  } catch (error) { console.warn('Could not read download status',error); }
  finally {
    polling = false;
    if (enabled) timer = setTimeout(poll,jobs.value.some(active) ? 500 : 2000);
  }
}
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
      if (offered && offered.archive_path === archivePath) {
        installers.delete(offered.job_id);
        offered = null;
      }
    },
    open(game: GameRecord) { selectedGame.value = game; },
    close() { selectedGame.value = null; },
    jobFor(gameId: number) { return jobs.value.find(job=>job.game_id===gameId && active(job)); },
    startPolling() { if (!enabled) { enabled = true; void poll(); } },
    refresh() { if (timer) clearTimeout(timer); timer = undefined; void poll(); },
    stopPolling() { enabled = false; if (timer) clearTimeout(timer); timer = undefined; },
  };
}
