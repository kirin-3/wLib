<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import {computed, ref, useId, useTemplateRef, watch} from 'vue';
import {IconAlertTriangle, IconCheck, IconChevronRight, IconCircleCheck, IconCopy, IconDeviceFloppy, IconDownload, IconExternalLink, IconInfoCircle, IconLoader2, IconPuzzle, IconRefresh, IconSend, IconSettings, IconX} from '@tabler/icons-vue';
import {useRouter} from 'vue-router';
import {api, type DownloadSite, type DownloadSiteOption, type RpdlSearchResponse, type RpdlTorrent, type UpdateBuild, type UpdateMirror} from '../../services/api';
import {useUpdateDownloads} from '../../composables/useUpdateDownloads';
import {downloadFailureMessage, inAppMirror, needsExtensionSwitch, orderBuilds, pickerTargetVersion, pickerVersionLine, preselect, selectableBuild, settlePickerStart, siteUnavailable, suitable, visibleArchivePassword, type HostPlatform} from '../../utils/updateDownloads';
import UpdateBuildGroup from '../downloads/UpdateBuildGroup.vue';
import {isWishlisted} from '../../utils/libraryGames';
import {loadPlatformCapabilities} from '../../utils/platformCapabilities';
import {formatSize, formatUploadDate, keepTorrentChoice, RPDL_SETTINGS_ROUTE, rpdlCanChoose, rpdlMarks, rpdlNote, rpdlOpenByDefault, rpdlPreselected, rpdlPrimaryAction, rpdlPrimaryLabel, rpdlSummary, torrentSentMessage} from '../../utils/torrentDownloads';
import {addedNotice, FAILURE_ACTION_LABELS} from '../../utils/downloadQueue';
import {SITE_SETTINGS_ROUTE} from '../../utils/extensionSiteSettings';
import {notify} from '../../utils/toast';
import {confirmAction} from '../../utils/confirm';
import {copyText} from '../../utils/copyText';
import {useModalKeyboard} from '../../utils/modalKeyboard';
import {onRadioGroupKeydown, radioTabStop} from '../../utils/radioGroup';

const downloads = useUpdateDownloads();
const router = useRouter();
const game = downloads.selectedGame;
const builds = ref<UpdateBuild[]>([]);
const chosenBuild = ref<UpdateBuild | null>(null);
const chosenMirror = ref<UpdateMirror | null>(null);
const version = ref('');
const archivePassword = ref('');
const passwordCopied = ref(false);
let passwordCopyTimer: ReturnType<typeof setTimeout> | null = null;
const sites = ref<DownloadSiteOption[]>([]);
const activeSite = ref<DownloadSite | ''>('');
const siteNames = {f95: 'F95Zone', lc: 'LewdCorner'};
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
const modal = useTemplateRef<HTMLElement>("modal");
let generation = 0;
// One number per opened picker; a download start answers only the picker that sent it.
let pickerSession = 0;
const wishlisted = computed(() => !!game.value && isWishlisted(game.value));
const updateVersion = computed(() => game.value ? pickerTargetVersion(game.value) : '');
// "Installed 1.0 → 2.0" under the title; the loaded thread's version once known.
const versionLine = computed(() => game.value ? pickerVersionLine(game.value, version.value) : null);
const canSelect = (build: UpdateBuild) => selectableBuild(build, wishlisted.value);
const rpdlNoteText = computed(() => rpdlNote(rpdl.value));
// Signed out, the torrents show as information only; the file-host choice stays.
const torrentsChoosable = computed(() => rpdlCanChoose(rpdl.value));
const ordered = computed(()=>orderBuilds(builds.value,platform.value));
// The RPDL section starts closed and opens by itself only when RPDL is the best
// source (utils/torrentDownloads.ts rpdlOpenByDefault). It never closes by
// itself, so the layout does not jump under the user; after the user opens or
// closes it, their choice stays for this picker.
const rpdlOpen = ref(false);
let rpdlUserToggled = false;
const rpdlAutoOpen = computed(() => rpdlOpenByDefault({
  response: rpdl.value,
  torrentChosen: !!chosenTorrent.value,
  linksSettled: !loading.value,
  inAppBuild: !!preselect(builds.value, platform.value),
  wishlisted: wishlisted.value,
}));
watch(rpdlAutoOpen, open => { if (open && !rpdlUserToggled) rpdlOpen.value = true; });
function onRpdlToggle(event: Event) {
  const details = event.target;
  if (!(details instanceof HTMLDetailsElement) || details.open === rpdlOpen.value) return;
  rpdlUserToggled = true;
  rpdlOpen.value = details.open;
}
const rpdlSummaryInfo = computed(() => rpdlSummary(rpdl.value, {loading: rpdlLoading.value, error: rpdlError.value, chosen: !!chosenTorrent.value}));
const onlySuitablePatches = computed(() => wishlisted.value && ordered.value.suitable.length > 0
  && ordered.value.suitable.every(build => !canSelect(build)));
const close = () => { generation++; rpdlGeneration++; pickerSession++; if (passwordCopyTimer) { clearTimeout(passwordCopyTimer); passwordCopyTimer = null; } passwordCopied.value = false; if (game.value) void api.closeUpdateDownloadLinks(game.value.id); downloads.close(); };
const openSiteSettings = () => {
  close();
  void router.push(SITE_SETTINGS_ROUTE);
};
const openRpdlSettings = () => {
  close();
  void router.push(RPDL_SETTINGS_ROUTE);
};
const showExtensionHelp = computed(() => needsExtensionSwitch(error.value, undefined, activeSite.value || undefined));
useModalKeyboard(modal,()=>!!game.value,close);
async function loadLinks(site: DownloadSite | '' = activeSite.value) {
  if (!game.value) return;
  const current = ++generation;
  activeSite.value = site;
  loading.value = true; error.value = ''; version.value = ''; archivePassword.value = ''; passwordCopied.value = false; builds.value = [];
  chosenBuild.value = null; chosenMirror.value = null;
  // A selected torrent remains selected when changing thread sites.
  userChose = !!chosenTorrent.value;
  try {
    const [response, capabilities] = await Promise.all([
      api.getUpdateDownloadLinks(game.value.id, site), loadPlatformCapabilities(),
    ]);
    if (current !== generation) return;
    if (response.sites) sites.value = response.sites;
    // The asked-for site is gone (LC turned off, or an LC Plus thread): show the default site.
    if (!response.success && !response.site && siteUnavailable(site, response.sites)) { void loadLinks(''); return; }
    if (response.site) activeSite.value = response.site;
    if (!response.success) throw new Error(response.error || 'Could not load links');
    platform.value = capabilities.platform === 'windows' ? 'windows' : 'linux';
    builds.value = response.builds || []; version.value = response.version || '';
    archivePassword.value = visibleArchivePassword(response.archive_password);
    const choice = preselect(builds.value,platform.value);
    if (!userChose && !chosenTorrent.value) { chosenBuild.value = choice?.build || null; chosenMirror.value = choice?.mirror || null; }
  } catch (e) { if (current === generation) error.value = String(e instanceof Error ? e.message : e); }
  finally {
    if (current === generation) {
      loading.value = false;
      if (wishlisted.value) preselectTorrent();
    }
  }
}
function openPicker() {
  pickerSession++;
  rpdlOpen.value = false; rpdlUserToggled = false;
  // A start still running for a closed picker does not block this one.
  busy.value = false;
  sites.value = [];
  chosenTorrent.value = null;
  userChose = false;
  exportNotice.value = ''; exportError.value = ''; sentMessage.value = '';
  void loadLinks(downloads.initialSite.value);
  void loadRpdl(false);
  void loadExportSettings();
}
function selectSite(site: DownloadSite) {
  if (site !== activeSite.value) void loadLinks(site);
}
function preselectTorrent() {
  if (userChose || (wishlisted.value && (loading.value || onlySuitablePatches.value))) return;
  const clear = rpdlPreselected(rpdl.value);
  if (clear) { chosenTorrent.value = clear; chosenMirror.value = null; chosenBuild.value = null; }
}
// A new RPDL answer (signed out, failed, or without the picked torrent) can make
// the picked torrent unusable. Drop it and go back to the file-host preselection,
// so the footer always keeps a way to download.
function dropUnusableTorrent() {
  if (!chosenTorrent.value || keepTorrentChoice(rpdl.value, chosenTorrent.value.id)) return;
  chosenTorrent.value = null; exportNotice.value = ''; exportError.value = ''; sentMessage.value = '';
  // While links load, no mirror can be picked yet: let loadLinks preselect one.
  if (loading.value) { userChose = false; return; }
  if (chosenMirror.value) return;
  const choice = preselect(builds.value, platform.value);
  chosenBuild.value = choice?.build || null; chosenMirror.value = choice?.mirror || null;
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
    preselectTorrent();
  } catch (e) { if (current === rpdlGeneration) rpdlError.value = String(e instanceof Error ? e.message : e); }
  finally { if (current === rpdlGeneration) { rpdlLoading.value = false; dropUnusableTorrent(); } }
}
async function loadExportSettings() {
  try {
    const settings = await api.getSettings();
    watchFolder.value = settings.torrent_watch_folder || '';
    exportWarningAccepted.value = settings.torrent_export_warning_accepted === true;
  } catch { watchFolder.value = ''; exportWarningAccepted.value = false; }
}
watch(game, current => { if (current) openPicker(); });
function choose(build: UpdateBuild, mirror: UpdateMirror) {
  if (!canSelect(build)) return;
  userChose=true; chosenTorrent.value=null; chosenBuild.value=build; chosenMirror.value=mirror; exportNotice.value=''; exportError.value=''; sentMessage.value='';
}
function chooseTorrent(torrent: RpdlTorrent) { if (!torrentsChoosable.value) return; userChose=true; chosenTorrent.value=torrent; chosenBuild.value=null; chosenMirror.value=null; exportNotice.value=''; exportError.value=''; sentMessage.value=''; }
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
  if (!game.value || busy.value || exporting.value || !canDownload.value) return;
  if (chosenTorrent.value && !inApp && primaryAction.value === 'send') { await sendToClient(); return; }
  busy.value=true; error.value=''; exportError.value=''; exportNotice.value=''; sentMessage.value='';
  const current = game.value;
  const replaces = downloads.replacing.value;
  const session = pickerSession;
  const stillOpen = () => session === pickerSession;
  try {
    const request = chosenTorrent.value
      ? api.startRpdlDownload(current.id,chosenTorrent.value.id,updateVersion.value,replaces)
      : api.startUpdateDownload(current.id,chosenMirror.value!.link_id,replaces);
    await settlePickerStart(request, {
      stillOpen,
      refresh: () => { downloads.refresh(); },
      close,
      notify: () => {
        const settings = downloads.queue.value?.settings;
        const waits = !!settings?.download_pause_while_playing && downloads.runningGames.value.includes(current.id);
        notify(addedNotice(current.title, waits), 'success', '', {label: 'Open Downloads', run: () => { void router.push('/downloads'); }});
      },
      showError: message => { error.value = message; },
    });
  } finally { if (stillOpen()) busy.value=false; }
}
async function openBrowser() {
  const current = game.value;
  if (!current || (!chosenMirror.value && !onlySuitablePatches.value)) return;
  error.value='';
  try {
    const response = chosenMirror.value
      ? await api.openUpdateDownloadInBrowser(current.id,chosenMirror.value.link_id)
      : await api.openInBrowser((activeSite.value === 'lc' ? current.lc_url : current.f95_url) || '');
    if (!response.success) error.value=response.error || 'Could not open browser';
  } catch (e) { error.value=String(e instanceof Error ? e.message : e) || 'Could not open browser'; }
}
const canDownloadMirror = computed(()=>!!chosenMirror.value && !!chosenBuild.value && canSelect(chosenBuild.value) && suitable(chosenBuild.value,platform.value) && inAppMirror(chosenMirror.value));
const canDownloadTorrent = computed(()=>!!chosenTorrent.value && !!rpdl.value?.signed_in);
const canDownload = computed(()=>canDownloadMirror.value || canDownloadTorrent.value);
// Mirrors and torrents are one single choice. Each build's mirrors are one
// radio group labelled by the build's heading row (UpdateBuildGroup.vue), and
// the RPDL torrents are another; checking a radio in one group unchecks the
// others. Each group is one Tab stop (see utils/radioGroup.ts).
const radioIds = useId();
const buildGroupId = (list: string, index: number) => `${radioIds}-${list}-${index}`;
const torrentTabIndex = (index: number) => {
  const results = rpdl.value?.results ?? [];
  const checked = results.findIndex(torrent => torrent.id === chosenTorrent.value?.id);
  return radioTabStop(checked, results.map(() => !torrentsChoosable.value)) === index ? 0 : -1;
};
async function copyArchivePassword() {
  if (!archivePassword.value) return;
  if (!(await copyText(archivePassword.value))) {
    notify('Could not copy the password. Select it and copy it by hand.', 'error');
    return;
  }
  passwordCopied.value = true;
  if (passwordCopyTimer) clearTimeout(passwordCopyTimer);
  passwordCopyTimer = setTimeout(() => { passwordCopied.value = false; passwordCopyTimer = null; }, 1800);
}
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
          <h3 id="download-title" class="text-xl font-bold" style="color: var(--text-primary)">{{ wishlisted ? 'Download game' : 'Download update' }}</h3>
          <p class="text-sm mt-1 truncate" style="color: var(--text-secondary)">{{ game.title }}</p>
          <p v-if="versionLine" class="text-xs mt-1 version-line" data-testid="picker-version">
            <span>{{ versionLine.label }}</span>
            <span class="font-mono">
              <template v-if="versionLine.from">
                <span class="whitespace-nowrap">{{ versionLine.from }}<span class="mx-1" aria-hidden="true">→</span><span class="sr-only">to </span></span>
                <span class="version-new font-semibold">{{ versionLine.to }}</span>
              </template>
              <span v-else class="version-only">{{ versionLine.to }}</span>
            </span>
          </p>
        </div>
        <button type="button" @click="close" aria-label="Close" title="Close (Esc)" class="close-btn ui-icon-btn shrink-0 -mr-2 -mt-1 rounded-lg">
          <IconX class="w-5 h-5" aria-hidden="true" />
        </button>
      </header>

      <div class="p-6 space-y-5 overflow-y-auto flex-1 min-h-0">
        <!-- Toggle buttons, not radios or tabs: a site button reloads that thread's
             links (an action), and the RPDL list below does not change with it. -->
        <div v-if="sites.length > 1" class="flex flex-wrap gap-2" role="group" aria-label="Download site">
          <button
            v-for="option in sites"
            :key="option.site"
            type="button"
            class="mirror site-option"
            :class="{ selected: activeSite === option.site }"
            :aria-pressed="activeSite === option.site"
            :disabled="busy || exporting"
            @click="selectSite(option.site)"
          >
            <span>{{ siteNames[option.site] }}</span>
            <span class="chip font-mono">{{ option.version || 'Unknown' }}</span>
            <span v-if="option.older" class="chip chip-warn">Older</span>
          </button>
        </div>
        <div v-if="loading" class="flex items-center gap-3 py-8 justify-center text-sm" style="color: var(--text-muted)">
          <IconLoader2 class="w-5 h-5 animate-spin" aria-hidden="true" />
          Loading download links…
        </div>

        <div v-else-if="error && !builds.length" class="callout callout-danger">
          <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          <div class="flex-1 min-w-0">
            <p>{{ downloadFailureMessage(error, activeSite || undefined) }}</p>
            <div class="flex flex-wrap gap-2 mt-3">
              <button v-if="showExtensionHelp" type="button" class="ui-card-btn modal-btn" @click="openSiteSettings">
                <IconPuzzle class="w-4 h-4" aria-hidden="true" />{{ FAILURE_ACTION_LABELS.extension }}
              </button>
              <button type="button" class="ui-card-btn modal-btn" @click="loadLinks()"><IconRefresh class="w-4 h-4" aria-hidden="true" />Retry</button>
            </div>
          </div>
        </div>

        <template v-else>
          <div v-if="archivePassword" class="flex items-center gap-2 flex-wrap text-sm" data-testid="archive-password-row">
            <span style="color: var(--text-secondary)">Archive password</span>
            <code class="font-mono px-2 py-0.5 rounded select-all break-all" style="background: var(--bg-overlay); color: var(--text-primary)">{{ archivePassword }}</code>
            <button type="button" class="ui-card-btn modal-btn modal-btn-quiet modal-btn-small shrink-0" @click="copyArchivePassword">
              <IconCheck v-if="passwordCopied" class="w-4 h-4" aria-hidden="true" />
              <IconCopy v-else class="w-4 h-4" aria-hidden="true" />
              {{ passwordCopied ? 'Copied' : 'Copy' }}
            </button>
          </div>
          <p v-if="!builds.length" class="text-sm py-6 text-center" style="color: var(--text-muted)">No download links found on the game's page.</p>

          <UpdateBuildGroup
            v-for="(build, buildIndex) in ordered.suitable"
            :key="build.label + build.section"
            :build="build"
            :group-id="buildGroupId('suitable', buildIndex)"
            :selectable="canSelect(build)"
            :chosen-link-id="chosenMirror?.link_id ?? null"
            :platform="platform"
            @choose="choose(build, $event)"
          />

          <details v-if="ordered.other.length" class="other">
            <summary class="flex items-center gap-1.5 text-sm cursor-pointer select-none" style="color: var(--text-secondary)">
              <IconChevronRight class="chevron w-4 h-4" aria-hidden="true" />
              Other platforms ({{ ordered.other.length }})
            </summary>
            <div class="space-y-4 mt-3">
              <UpdateBuildGroup
                v-for="(build, buildIndex) in ordered.other"
                :key="build.label + build.section"
                :build="build"
                :group-id="buildGroupId('other', buildIndex)"
                :selectable="canSelect(build)"
                :chosen-link-id="chosenMirror?.link_id ?? null"
                :platform="platform"
                @choose="choose(build, $event)"
              />
            </div>
          </details>

          <details v-if="ordered.old.length" class="other" data-testid="older-versions">
            <summary class="flex items-center gap-1.5 text-sm cursor-pointer select-none" style="color: var(--text-secondary)">
              <IconChevronRight class="chevron w-4 h-4" aria-hidden="true" />
              Older versions ({{ ordered.old.length }})
            </summary>
            <div class="space-y-4 mt-3">
              <UpdateBuildGroup
                v-for="(build, buildIndex) in ordered.old"
                :key="build.label + build.section"
                :build="build"
                :group-id="buildGroupId('old', buildIndex)"
                :selectable="canSelect(build)"
                :chosen-link-id="chosenMirror?.link_id ?? null"
                :platform="platform"
                @choose="choose(build, $event)"
              />
            </div>
          </details>

          <div v-if="chosenBuild?.kind === 'old'" class="callout callout-warn" data-testid="older-version-note">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <p>This is an older version than the thread's current one ({{ version || 'Unknown' }}).</p>
          </div>
          <div v-if="chosenBuild?.kind === 'patch'" class="callout callout-warn">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <p>This patch updates an existing install. Keep the old version when installing it.</p>
          </div>
          <div v-if="chosenMirror?.host === 'buzzheavier'" class="callout">
            <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div class="flex-1 min-w-0">
              <p>Enable Buzzheavier downloads in the extension settings.</p>
              <button type="button" class="ui-card-btn modal-btn mt-2" @click="openSiteSettings">
                <IconPuzzle class="w-4 h-4" aria-hidden="true" />{{ FAILURE_ACTION_LABELS.extension }}
              </button>
            </div>
          </div>
          <div v-if="error" role="alert" class="callout callout-danger">
            <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div class="flex-1 min-w-0">
              <p>{{ downloadFailureMessage(error, activeSite || undefined) }}</p>
              <button v-if="showExtensionHelp" type="button" class="ui-card-btn modal-btn mt-2" @click="openSiteSettings">
                <IconPuzzle class="w-4 h-4" aria-hidden="true" />{{ FAILURE_ACTION_LABELS.extension }}
              </button>
            </div>
          </div>

        </template>

        <!-- Closed unless RPDL is the best source (see rpdlAutoOpen). The summary
             keeps the count and state visible while it is closed. -->
        <details class="other" :open="rpdlOpen" data-testid="rpdl-section" @toggle="onRpdlToggle">
          <summary class="flex items-center gap-1.5 text-sm cursor-pointer select-none" style="color: var(--text-secondary)">
            <IconChevronRight class="chevron w-4 h-4" aria-hidden="true" />
            <span>RPDL torrents<template v-if="rpdlSummaryInfo.count"> ({{ rpdlSummaryInfo.count }})</template></span>
            <span v-if="rpdlSummaryInfo.state" class="summary-state" :class="`summary-${rpdlSummaryInfo.tone}`" data-testid="rpdl-summary-state">{{ rpdlSummaryInfo.state }}</span>
          </summary>
          <div class="space-y-2 mt-3">
            <!-- Names the torrent radio group without the count and state. -->
            <span id="rpdl-heading" hidden>RPDL torrents</span>
            <div v-if="rpdlLoading" class="flex items-center gap-2 text-sm py-2" style="color: var(--text-muted)">
              <IconLoader2 class="w-4 h-4 animate-spin" aria-hidden="true" />Searching RPDL…
            </div>
            <div v-else-if="rpdlError" class="callout callout-danger" role="alert">
              <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
              <div class="flex-1 min-w-0">
                <p>{{ rpdlError }}</p>
                <button type="button" class="ui-card-btn modal-btn mt-3" @click="loadRpdl(true)"><IconRefresh class="w-4 h-4" aria-hidden="true" />Retry</button>
              </div>
            </div>
            <p v-else-if="!rpdl?.results?.length" class="text-sm" style="color: var(--text-muted)">No matching torrents on RPDL.</p>
            <template v-else>
              <div v-if="!torrentsChoosable" id="rpdl-sign-in-note" class="callout items-center justify-between gap-3 flex-wrap" data-testid="rpdl-sign-in">
                <div class="flex items-start gap-2 min-w-0">
                  <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                  <p>{{ rpdlNoteText }}</p>
                </div>
                <button type="button" class="ui-card-btn modal-btn shrink-0" @click="openRpdlSettings"><IconSettings class="w-4 h-4" aria-hidden="true" />Open RPDL settings</button>
              </div>
              <div class="torrent-list" role="radiogroup" aria-labelledby="rpdl-heading" :aria-disabled="!torrentsChoosable || undefined" @keydown="onRadioGroupKeydown">
                <button
                  v-for="(torrent, torrentIndex) in rpdl.results"
                  :key="torrent.id"
                  type="button"
                  class="mirror torrent"
                  :class="{ selected: chosenTorrent?.id === torrent.id }"
                  :disabled="!torrentsChoosable"
                  role="radio"
                  :aria-checked="chosenTorrent?.id === torrent.id"
                  :tabindex="torrentTabIndex(torrentIndex)"
                  :aria-describedby="torrentsChoosable ? undefined : 'rpdl-sign-in-note'"
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
                <button type="button" class="ui-card-btn modal-btn" :disabled="exporting || busy" @click="saveTorrentFile"><IconDeviceFloppy class="w-4 h-4" aria-hidden="true" />Save .torrent</button>
                <button type="button" class="ui-card-btn modal-btn" :disabled="exporting || busy" @click="copyMagnet"><IconCopy class="w-4 h-4" aria-hidden="true" />Copy magnet</button>
                <IconLoader2 v-if="exporting" class="w-4 h-4 animate-spin" style="color: var(--text-muted)" aria-hidden="true" />
                <p v-if="exportNotice" class="text-xs min-w-0 break-all" style="color: var(--text-secondary)" role="status">{{ exportNotice }}</p>
              </div>
              <div v-if="exportError" class="callout callout-danger" role="alert">
                <IconAlertTriangle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                <p class="break-words min-w-0">{{ exportError }}</p>
              </div>
              <div v-if="sentMessage" class="callout callout-success" role="status">
                <IconCircleCheck class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                <p class="break-words min-w-0">{{ sentMessage }}</p>
              </div>
            </template>
            <button v-if="!rpdlLoading && !rpdlError" type="button" class="search-again" @click="loadRpdl(true)"><IconRefresh class="w-3.5 h-3.5" aria-hidden="true" />Search RPDL again</button>
          </div>
        </details>

      </div>

      <footer class="picker-footer" data-testid="update-download-footer" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
        <button
          type="button"
          class="ui-card-btn modal-btn modal-btn-quiet"
          :class="{ 'footer-wide': loading || !(chosenMirror || onlySuitablePatches) }"
          @click="close"
        >Close</button>
        <template v-if="!loading">
          <button v-if="chosenMirror || onlySuitablePatches" type="button" class="ui-card-btn modal-btn" @click="openBrowser"><IconExternalLink class="w-4 h-4 shrink-0" aria-hidden="true" />Open in browser</button>
          <button
            v-if="canDownloadTorrent && primaryAction === 'send'"
            type="button"
            class="ui-card-btn ui-card-btn--link link-btn footer-wide"
            :disabled="busy || exporting"
            @click="start(true)"
          >Download in wLib instead</button>
          <button
            v-if="canDownload || onlySuitablePatches"
            :disabled="!canDownload || busy || exporting"
            @click="start()"
            type="button"
            class="ui-card-btn ui-card-btn--primary modal-btn modal-btn-primary footer-wide"
          >
            <IconLoader2 v-if="busy || (exporting && primaryAction === 'send')" class="w-4 h-4 animate-spin" aria-hidden="true" />
            <IconSend v-else-if="primaryAction === 'send'" class="w-4 h-4" aria-hidden="true" />
            <IconDownload v-else class="w-4 h-4" aria-hidden="true" />
            {{ rpdlPrimaryLabel(primaryAction) }}
          </button>
        </template>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.site-option {
  gap: 0.5rem;
  width: auto;
}
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}
.close-btn { color: var(--text-muted); }
.close-btn:hover { color: var(--text-primary); background: var(--surface-hover); }

/* "Installed 1.0 → 2.0", as in the Updates rows: the old version muted, the new one in the success colour. */
.version-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
  min-width: 0;
  color: var(--text-muted);
}
.version-line .font-mono { color: var(--text-secondary); min-width: 0; overflow-wrap: anywhere; }
.version-new { color: var(--success-text); }
.version-only { color: var(--text-primary); }

/* Card, radio and chip styles are :deep so the build groups (UpdateBuildGroup.vue) share them. */
:deep(.chip) {
  padding: 0.05rem 0.45rem;
  border-radius: 0.375rem;
  font-size: 0.7rem;
  font-weight: 500;
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
:deep(.chip-good) {
  background: var(--success-bg, var(--bg-overlay));
  color: var(--success-text, var(--text-secondary));
  border-color: var(--success-border, var(--border));
}
:deep(.chip-warn) {
  background: var(--tag-yellow-bg);
  color: var(--tag-yellow-text);
  border-color: var(--tag-yellow-border);
}

.torrent-list { display: grid; gap: 0.5rem; }
:deep(.mirror) {
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
:deep(.mirror:disabled) { opacity: 0.6; cursor: not-allowed; }
:deep(.mirror:hover:not(:disabled)) { background: var(--bg-overlay); border-color: var(--border-hover); }
/* Selected: a 2px accent edge and the filled radio. Keyboard focus: a separate
   neutral ring with a gap, so a focused card that is not chosen never looks chosen.
   No accent tint: muted card text keeps 4.5:1 on the raised fill. */
:deep(.mirror.selected),
:deep(.mirror.selected:hover:not(:disabled)) {
  border-color: var(--brand);
  box-shadow: inset 0 0 0 1px var(--brand);
}
/* Neutral like the tag pills' focus ring (style.css), so it never reads as the accent "selected" edge. */
:deep(.mirror:focus-visible) { outline: 2px solid var(--text-primary); outline-offset: 3px; }
:deep(.radio) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.1rem;
  height: 1.1rem;
  flex-shrink: 0;
  border-radius: 9999px;
  border: 1.5px solid var(--border-hover);
}
:deep(.mirror.selected .radio) {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
}

.other summary { list-style: none; }
.other summary::-webkit-details-marker { display: none; }
.other summary:hover { color: var(--text-primary); }
.chevron { transition: transform var(--motion-fast) ease; }
.other[open] > summary .chevron { transform: rotate(90deg); }
/* The RPDL summary's short state ("Sign in needed", "Matches version"). */
.summary-state {
  margin-left: 0.25rem;
  font-size: 0.75rem;
  color: var(--text-muted);
}
.summary-warn { color: var(--warning-text); }
.summary-good { color: var(--success-text, var(--text-secondary)); }
.search-again {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.25rem 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}
.search-again:hover { color: var(--text-primary); }

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
/* "Download in wLib instead": the shared text action, at the footer's text size. */
.link-btn { font-size: 0.875rem; }

/* The shared .ui-card-btn set (colours, hover, disabled) at the dialog's text size. */
.modal-btn {
  gap: 0.4rem;
  font-size: 0.875rem;
  line-height: 1.25rem;
  font-weight: 500;
}
.modal-btn-primary { padding-inline: 1.5rem; font-weight: 700; box-shadow: var(--shadow-brand); }
/* Close: the same shape as the other footer buttons, without the fill. */
.modal-btn-quiet { background: transparent; border-color: var(--border); color: var(--text-secondary); }
.modal-btn-quiet:hover:not(:disabled) { background: var(--bg-overlay); color: var(--text-primary); }
.modal-btn-small { padding: 0.25rem 0.6rem; font-size: 0.8rem; line-height: 1rem; }

/* One row on wide windows. Below 640px: Close and Open in browser share a row,
   the torrent fallback link and the primary button take full rows under them,
   in DOM (and tab) order. Labels never break inside a button. */
.picker-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
}
.picker-footer > * { white-space: nowrap; min-height: 2.5rem; }
.picker-footer > .link-btn { min-height: 0; }
@media (max-width: 639px) {
  .picker-footer {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.5rem;
    padding: 0.75rem 1rem;
  }
  .picker-footer > .footer-wide { grid-column: 1 / -1; }
  .picker-footer > .link-btn { justify-self: center; padding: 0.25rem 0; }
  .picker-footer > .modal-btn { padding-inline: 0.75rem; }
}

</style>
