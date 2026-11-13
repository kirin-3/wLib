<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import {computed, ref, watch} from 'vue';
import {IconAlertTriangle, IconCheck, IconChevronRight, IconCircleCheck, IconCopy, IconDeviceFloppy, IconDownload, IconExternalLink, IconInfoCircle, IconLoader2, IconRefresh, IconSend, IconX} from '@tabler/icons-vue';
import {useRouter} from 'vue-router';
import {api, type RpdlSearchResponse, type RpdlTorrent, type UpdateBuild, type UpdateMirror} from '../../services/api';
import {useUpdateDownloads} from '../../composables/useUpdateDownloads';
import {downloadFailureMessage, inAppMirror, orderBuilds, preselect, suitable, type HostPlatform} from '../../utils/updateDownloads';
import {loadPlatformCapabilities} from '../../utils/platformCapabilities';
import {formatSize, formatUploadDate, rpdlMarks, rpdlNote, rpdlPreselected, rpdlPrimaryAction, rpdlPrimaryLabel, torrentSentMessage} from '../../utils/torrentDownloads';
import {addedNotice} from '../../utils/downloadQueue';
import {notify} from '../../utils/toast';
import {confirmAction} from '../../utils/confirm';
import {copyText} from '../../utils/copyText';
import {useModalKeyboard} from '../../utils/modalKeyboard';

const downloads = useUpdateDownloads();
const router = useRouter();
const game = downloads.selectedGame;
const builds = ref<UpdateBuild[]>([]);
const chosenBuild = ref<UpdateBuild | null>(null);
const chosenMirror = ref<UpdateMirror | null>(null);
const version = ref('');
const error = ref('');
const loading = ref(false);
const rpdl = ref<RpdlSearchResponse | null>(null);
const rpdlLoading = ref(false);
const rpdlError = ref('');
const chosenTorrent = ref<RpdlTorrent | null>(null);
// Set once the user picks something, so a late RPDL answer never changes the choice.
let userChose = false;
let rpdlGeneration = 0;
const busy = ref(false);
// Device-local torrent export settings, read each time the dialog opens.
const watchFolder = ref('');
const exportWarningAccepted = ref(false);
const exporting = ref(false);
const exportNotice = ref('');
const exportError = ref('');
const sentMessage = ref('');
const primaryAction = computed(()=>chosenTorrent.value ? rpdlPrimaryAction(watchFolder.value) : 'download');
const platform = ref<HostPlatform>('linux');
const modal = ref<HTMLElement | null>(null);
let generation = 0;
const updateVersion = computed(() => (game.value?.latest_version || '').trim());
const rpdlNoteText = computed(() => rpdlNote(rpdl.value));
const ordered = computed(()=>orderBuilds(builds.value,platform.value));
const close = () => { generation++; if (game.value) void api.closeUpdateDownloadLinks(game.value.id); downloads.close(); };
useModalKeyboard(modal,()=>!!game.value,close);
async function load() {
  if (!game.value) return;
  const current = ++generation;
  loading.value = true; error.value = ''; version.value = ''; builds.value = []; chosenBuild.value = null; chosenMirror.value = null;
  chosenTorrent.value = null; userChose = false;
  exportNotice.value = ''; exportError.value = ''; sentMessage.value = '';
  void loadRpdl(false);
  void loadExportSettings();
  try {
    const [response, capabilities] = await Promise.all([api.getUpdateDownloadLinks(game.value.id),loadPlatformCapabilities()]);
    if (current !== generation) return;
    if (!response.success) throw new Error(response.error || 'Could not load links');
    platform.value = capabilities.platform === 'windows' ? 'windows' : 'linux';
    builds.value = response.builds || []; version.value = response.version || 'Unknown';
    const choice = preselect(builds.value,platform.value);
    if (!userChose && !chosenTorrent.value) { chosenBuild.value = choice?.build || null; chosenMirror.value = choice?.mirror || null; }
  } catch (e) { if (current === generation) error.value = String(e instanceof Error ? e.message : e); }
  finally { if (current === generation) loading.value = false; }
}
// RPDL loads on its own, so a failure here never hides the thread's builds and the reverse.
async function loadRpdl(refresh: boolean) {
  if (!game.value) return;
  const current = ++rpdlGeneration;
  rpdlLoading.value = true; rpdlError.value = ''; rpdl.value = null;
  try {
    const response = await api.findRpdlTorrents(game.value.id, updateVersion.value, refresh);
    if (current !== rpdlGeneration) return;
    if (!response.success) throw new Error(response.error || 'Could not search RPDL');
    rpdl.value = response;
    const clear = rpdlPreselected(response);
    if (clear && !userChose) { chosenTorrent.value = clear; chosenMirror.value = null; chosenBuild.value = null; }
  } catch (e) { if (current === rpdlGeneration) rpdlError.value = String(e instanceof Error ? e.message : e); }
  finally { if (current === rpdlGeneration) rpdlLoading.value = false; }
}
async function loadExportSettings() {
  try {
    const settings = await api.getSettings();
    watchFolder.value = settings.torrent_watch_folder || '';
    exportWarningAccepted.value = settings.torrent_export_warning_accepted === true;
  } catch { watchFolder.value = ''; exportWarningAccepted.value = false; }
}
watch(game, current => { if (current) void load(); });
function choose(build: UpdateBuild, mirror: UpdateMirror) { userChose=true; chosenTorrent.value=null; chosenBuild.value=build; chosenMirror.value=mirror; exportNotice.value=''; exportError.value=''; sentMessage.value=''; }
function chooseTorrent(torrent: RpdlTorrent) { userChose=true; chosenTorrent.value=torrent; chosenBuild.value=null; chosenMirror.value=null; exportNotice.value=''; exportError.value=''; sentMessage.value=''; }
// The one-time passkey warning. True when the export may run.
async function passkeyWarningAccepted(): Promise<boolean> {
  if (exportWarningAccepted.value) return true;
  const accepted = await confirmAction({
    title: 'Keep your passkey private',
    message: 'The .torrent file and the magnet link contain your private RPDL passkey. Do not share them. Anyone with the passkey can use your RPDL account.',
    confirmLabel: 'I understand',
  });
  if (!accepted) return false;
  exportWarningAccepted.value = true;
  try { await api.saveSettings({torrent_export_warning_accepted: true}); } catch { /* The warning shows again next time. */ }
  return true;
}
// Runs one export action for the chosen torrent, after the passkey warning.
async function runExport(action: (torrentId: number) => Promise<void>) {
  const torrent = chosenTorrent.value;
  if (!game.value || !torrent || exporting.value || busy.value) return;
  error.value = ''; exportError.value = ''; exportNotice.value = ''; sentMessage.value = '';
  if (!(await passkeyWarningAccepted())) return;
  exporting.value = true;
  try { await action(torrent.id); }
  catch (e) { exportError.value = String(e instanceof Error ? e.message : e); }
  finally { exporting.value = false; }
}
const saveTorrentFile = () => runExport(async torrentId => {
  const folder = await api.browseDirectory('');
  if (!folder) return;
  const response = await api.saveRpdlTorrent(torrentId, folder);
  if (!response.success) throw new Error(response.error || 'Could not save the .torrent file');
  exportNotice.value = `Saved ${response.path || 'the .torrent file'}`;
});
const copyMagnet = () => runExport(async torrentId => {
  const response = await api.getRpdlMagnet(torrentId);
  if (!response.success || !response.magnet) throw new Error(response.error || 'Could not get the magnet link');
  if (!(await copyText(response.magnet))) throw new Error('Could not copy the magnet link to the clipboard');
  exportNotice.value = 'Magnet link copied';
});
const sendToClient = () => runExport(async torrentId => {
  const response = await api.sendRpdlTorrentToWatchFolder(torrentId);
  if (!response.success) throw new Error(response.error || 'Could not send the torrent to the watch folder');
  sentMessage.value = torrentSentMessage(response.folder || watchFolder.value);
});
async function start(inApp = false) {
  if (!game.value || busy.value || exporting.value || (!chosenMirror.value && !chosenTorrent.value)) return;
  if (chosenTorrent.value && !inApp && primaryAction.value === 'send') { await sendToClient(); return; }
  busy.value=true; error.value=''; exportError.value=''; exportNotice.value=''; sentMessage.value='';
  const current = game.value;
  const replaces = downloads.replacing.value;
  try {
    const response = chosenTorrent.value
      ? await api.startRpdlDownload(current.id,chosenTorrent.value.id,updateVersion.value,replaces)
      : await api.startUpdateDownload(current.id,chosenMirror.value!.link_id,replaces);
    // On a refusal the picker stays open and shows why.
    if (!response.success) throw new Error(response.error || 'Could not start download');
    downloads.refresh();
    close();
    const settings = downloads.queue.value?.settings;
    const waits = !!settings?.download_pause_while_playing && downloads.runningGames.value.includes(current.id);
    notify(addedNotice(current.title, waits), 'success', '', {label: 'Open Downloads', run: () => { void router.push('/downloads'); }});
  } catch (e) { error.value=String(e instanceof Error ? e.message : e); }
  finally { busy.value=false; }
}
async function openBrowser() {
  if (!game.value || !chosenMirror.value) return;
  error.value='';
  try {
    const response = await api.openUpdateDownloadInBrowser(game.value.id,chosenMirror.value.link_id);
    if (!response.success) error.value=response.error || 'Could not open browser';
  } catch (e) { error.value=String(e instanceof Error ? e.message : e) || 'Could not open browser'; }
}
const canDownloadMirror = computed(()=>!!chosenMirror.value && !!chosenBuild.value && suitable(chosenBuild.value,platform.value) && inAppMirror(chosenMirror.value));
const canDownloadTorrent = computed(()=>!!chosenTorrent.value && !!rpdl.value?.signed_in);
const canDownload = computed(()=>canDownloadMirror.value || canDownloadTorrent.value);
const buildTitle = (build: UpdateBuild) => (build.section ? build.section + ' · ' : '') + build.label;
const platformLabel = (value: string) => value === 'unknown' ? 'Any platform' : value === 'all' ? 'All platforms' : value.split('/').map(part=>part.charAt(0).toUpperCase()+part.slice(1)).join(' / ');
const showPlatform = (build: UpdateBuild) => platformLabel(build.platform).replace(/\s/g,'').toLowerCase() !== build.label.replace(/\s/g,'').toLowerCase();
</script>
<template>
  <div v-if="game" class="fixed inset-0 z-[100] flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
    <section
      ref="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="download-title"
      tabindex="-1"
      class="modal-content w-full max-w-2xl rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none"
    >
      <header class="px-6 py-5 flex items-start justify-between gap-4" style="border-bottom: 1px solid var(--border)">
        <div class="min-w-0">
          <h3 id="download-title" class="text-xl font-bold" style="color: var(--text-primary)">Download update</h3>
          <p class="text-sm mt-1 flex items-center gap-2 min-w-0" style="color: var(--text-secondary)">
            <span class="truncate">{{ game.title }}</span>
            <span v-if="version" class="chip font-mono shrink-0">{{ version }}</span>
          </p>
        </div>
        <button @click="close" aria-label="Close" title="Close (Esc)" class="close-btn shrink-0 p-1 rounded-lg">
          <IconX class="w-5 h-5" />
        </button>
      </header>

      <div class="p-6 space-y-5 overflow-y-auto flex-1 min-h-0">
        <div v-if="loading" class="flex items-center gap-3 py-8 justify-center text-sm" style="color: var(--text-muted)">
          <IconLoader2 class="w-5 h-5 animate-spin" />
          Loading download links…
        </div>

        <div v-else-if="error && !builds.length" class="callout callout-danger">
          <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
          <div class="flex-1 min-w-0">
            <p>{{ downloadFailureMessage(error) }}</p>
            <button class="modal-btn mt-3" @click="load"><IconRefresh class="w-4 h-4" />Retry</button>
          </div>
        </div>

        <template v-else>
          <p v-if="!builds.length" class="text-sm py-6 text-center" style="color: var(--text-muted)">No download links found on the game's page.</p>

          <div v-for="build in ordered.suitable" :key="build.label + build.section" class="space-y-2">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-sm font-semibold" style="color: var(--text-primary)">{{ buildTitle(build) }}</span>
              <span v-if="showPlatform(build)" class="chip">{{ platformLabel(build.platform) }}</span>
              <span v-if="build.kind === 'patch'" class="chip chip-warn">Patch</span>
            </div>
            <div class="mirror-grid">
              <button
                v-for="mirror in build.mirrors"
                :key="mirror.link_id"
                type="button"
                class="mirror"
                :class="{ selected: chosenMirror?.link_id === mirror.link_id }"
                :aria-pressed="chosenMirror?.link_id === mirror.link_id"
                @click="choose(build, mirror)"
              >
                <span class="min-w-0 flex-1 text-left">
                  <span class="block text-sm font-medium truncate">{{ mirror.label || mirror.host }}</span>
                  <span class="block text-xs truncate" style="color: var(--text-muted)">
                    <template v-if="mirror.split">Part {{ mirror.part }} · browser only</template>
                    <template v-else-if="inAppMirror(mirror)">In-app download</template>
                    <template v-else>Browser only</template>
                  </span>
                </span>
                <span class="radio" aria-hidden="true"><IconCheck v-if="chosenMirror?.link_id === mirror.link_id" class="w-3 h-3" stroke-width="3" /></span>
              </button>
            </div>
          </div>

          <details v-if="ordered.other.length" class="other">
            <summary class="flex items-center gap-1.5 text-sm cursor-pointer select-none" style="color: var(--text-secondary)">
              <IconChevronRight class="chevron w-4 h-4" />
              Other platforms ({{ ordered.other.length }})
            </summary>
            <div class="space-y-4 mt-3">
              <div v-for="build in ordered.other" :key="build.label + build.section" class="space-y-2">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-sm font-semibold" style="color: var(--text-primary)">{{ buildTitle(build) }}</span>
                  <span v-if="showPlatform(build)" class="chip">{{ platformLabel(build.platform) }}</span>
                </div>
                <div class="mirror-grid">
                  <button
                    v-for="mirror in build.mirrors"
                    :key="mirror.link_id"
                    type="button"
                    class="mirror"
                    :class="{ selected: chosenMirror?.link_id === mirror.link_id }"
                    :aria-pressed="chosenMirror?.link_id === mirror.link_id"
                    @click="choose(build, mirror)"
                  >
                    <span class="min-w-0 flex-1 text-left">
                      <span class="block text-sm font-medium truncate">{{ mirror.label || mirror.host }}</span>
                      <span class="block text-xs truncate" style="color: var(--text-muted)">Browser only</span>
                    </span>
                    <span class="radio" aria-hidden="true"><IconCheck v-if="chosenMirror?.link_id === mirror.link_id" class="w-3 h-3" stroke-width="3" /></span>
                  </button>
                </div>
              </div>
            </div>
          </details>

          <div v-if="chosenBuild?.kind === 'patch'" class="callout callout-warn">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
            <p>This patch updates an existing install. Keep the old version when installing it.</p>
          </div>
          <div v-if="chosenMirror?.host === 'buzzheavier'" class="callout">
            <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" />
            <p>Enable Buzzheavier downloads in the extension settings.</p>
          </div>
          <div v-if="error" role="alert" class="callout callout-danger">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
            <p>{{ downloadFailureMessage(error) }}</p>
          </div>

        </template>

        <section class="space-y-2" aria-labelledby="rpdl-heading" data-testid="rpdl-section">
          <div class="flex items-center gap-2">
            <h4 id="rpdl-heading" class="text-sm font-semibold" style="color: var(--text-primary)">RPDL torrents</h4>
            <button v-if="!rpdlLoading" type="button" class="icon-btn" aria-label="Search RPDL again" title="Search RPDL again" @click="loadRpdl(true)"><IconRefresh class="w-3.5 h-3.5" /></button>
          </div>
          <div v-if="rpdlLoading" class="flex items-center gap-2 text-sm py-2" style="color: var(--text-muted)">
            <IconLoader2 class="w-4 h-4 animate-spin" />Searching RPDL…
          </div>
          <div v-else-if="rpdlError" class="callout callout-danger" role="alert">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
            <div class="flex-1 min-w-0">
              <p>{{ rpdlError }}</p>
              <button type="button" class="modal-btn mt-3" @click="loadRpdl(true)"><IconRefresh class="w-4 h-4" />Retry</button>
            </div>
          </div>
          <p v-else-if="!rpdl?.results?.length" class="text-sm" style="color: var(--text-muted)">No matching torrents on RPDL.</p>
          <template v-else>
            <div class="torrent-list">
              <button
                v-for="torrent in rpdl.results"
                :key="torrent.id"
                type="button"
                class="mirror torrent"
                :class="{ selected: chosenTorrent?.id === torrent.id }"
                :aria-pressed="chosenTorrent?.id === torrent.id"
                @click="chooseTorrent(torrent)"
              >
                <span class="min-w-0 flex-1 text-left">
                  <span class="block text-sm font-medium truncate" :title="torrent.title">{{ torrent.title }}</span>
                  <span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs" style="color: var(--text-muted)">
                    <span>{{ formatSize(torrent.size) }}</span>
                    <span>{{ torrent.seeders }} seeders</span>
                    <span>{{ torrent.leechers }} leechers</span>
                    <span v-if="formatUploadDate(torrent.date)">{{ formatUploadDate(torrent.date) }}</span>
                    <span v-for="mark in rpdlMarks(torrent)" :key="mark.text" class="chip" :class="mark.tone === 'warn' ? 'chip-warn' : 'chip-good'">{{ mark.text }}</span>
                  </span>
                </span>
                <span class="radio" aria-hidden="true"><IconCheck v-if="chosenTorrent?.id === torrent.id" class="w-3 h-3" stroke-width="3" /></span>
              </button>
            </div>
            <div v-if="canDownloadTorrent" class="flex flex-wrap items-center gap-2" data-testid="rpdl-export-actions">
              <button type="button" class="modal-btn" :disabled="exporting || busy" @click="saveTorrentFile"><IconDeviceFloppy class="w-4 h-4" />Save .torrent</button>
              <button type="button" class="modal-btn" :disabled="exporting || busy" @click="copyMagnet"><IconCopy class="w-4 h-4" />Copy magnet</button>
              <IconLoader2 v-if="exporting" class="w-4 h-4 animate-spin" style="color: var(--text-muted)" />
              <p v-if="exportNotice" class="text-xs min-w-0 break-all" style="color: var(--text-secondary)" role="status">{{ exportNotice }}</p>
            </div>
            <div v-if="exportError" class="callout callout-danger" role="alert">
              <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" />
              <p class="break-words min-w-0">{{ exportError }}</p>
            </div>
            <div v-if="sentMessage" class="callout callout-success" role="status">
              <IconCircleCheck class="w-4 h-4 shrink-0 mt-0.5" />
              <p class="break-words min-w-0">{{ sentMessage }}</p>
            </div>
            <div v-if="rpdlNoteText" class="callout">
              <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" />
              <p>{{ rpdlNoteText }}</p>
            </div>
          </template>
        </section>

      </div>

      <footer class="px-6 py-4 flex items-center justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
        <button @click="close" class="px-5 py-2.5 rounded-lg text-sm font-medium" style="color: var(--text-secondary)">Close</button>
        <template v-if="!loading">
          <button v-if="chosenMirror" class="modal-btn" @click="openBrowser"><IconExternalLink class="w-4 h-4" />Open in browser</button>
          <button
            v-if="canDownloadTorrent && primaryAction === 'send'"
            type="button"
            class="link-btn text-sm"
            :disabled="busy || exporting"
            @click="start(true)"
          >Download in wLib instead</button>
          <button
            v-if="canDownload"
            :disabled="busy || exporting"
            @click="start()"
            class="primary-btn px-6 py-2.5 rounded-lg text-sm font-bold inline-flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <IconLoader2 v-if="busy || (exporting && primaryAction === 'send')" class="w-4 h-4 animate-spin" />
            <IconSend v-else-if="primaryAction === 'send'" class="w-4 h-4" />
            <IconDownload v-else class="w-4 h-4" />
            {{ rpdlPrimaryLabel(primaryAction) }}
          </button>
        </template>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}
.close-btn { color: var(--text-muted); }
.close-btn:hover { color: var(--text-primary); }

.chip {
  padding: 0.05rem 0.45rem;
  border-radius: 0.375rem;
  font-size: 0.7rem;
  font-weight: 500;
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.chip-good {
  background: var(--success-bg, var(--bg-overlay));
  color: var(--success-text, var(--text-secondary));
  border-color: var(--success-border, var(--border));
}
.chip-warn {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border-color: var(--tag-yellow-border);
}

.torrent-list { display: grid; gap: 0.5rem; }
.icon-btn { color: var(--text-muted); padding: 0.15rem; border-radius: 0.375rem; }
.icon-btn:hover { color: var(--text-primary); background: var(--bg-overlay); }
.mirror-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
  gap: 0.5rem;
}
.mirror {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border-radius: 0.6rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-primary);
  transition: border-color var(--motion-fast) ease, background var(--motion-fast) ease, box-shadow var(--motion-fast) ease;
}
.mirror:hover { background: var(--bg-overlay); border-color: var(--border-hover); }
.mirror.selected {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}
.mirror:focus-visible { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
.radio {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.1rem;
  height: 1.1rem;
  flex-shrink: 0;
  border-radius: 9999px;
  border: 1.5px solid var(--border-hover);
}
.mirror.selected .radio {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
}

.other summary { list-style: none; }
.other summary::-webkit-details-marker { display: none; }
.other summary:hover { color: var(--text-primary); }
.chevron { transition: transform var(--motion-fast) ease; }
.other[open] .chevron { transform: rotate(90deg); }

.callout {
  display: flex;
  gap: 0.6rem;
  padding: 0.7rem 0.85rem;
  border-radius: 0.6rem;
  font-size: 0.85rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.callout-warn { color: var(--warning-text); background: var(--tag-yellow-bg); border-color: var(--tag-yellow-border); }
.callout-danger { color: var(--danger-text); background: var(--danger-bg); border-color: var(--danger-border); }
.callout-success { color: var(--success-text, var(--text-secondary)); background: var(--success-bg, var(--bg-raised)); border-color: var(--success-border, var(--border)); }
.link-btn { color: var(--text-secondary); text-decoration: underline; text-underline-offset: 2px; }
.link-btn:hover:not(:disabled) { color: var(--text-primary); }
.link-btn:disabled { opacity: 0.5; cursor: not-allowed; }


.modal-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
}
.modal-btn:hover:not(:disabled) { background: var(--border-hover); }
.modal-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.primary-btn {
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
}
</style>
