<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import {computed, ref, watch} from 'vue';
import {api, type UpdateBuild, type UpdateMirror} from '../../services/api';
import {useUpdateDownloads} from '../../composables/useUpdateDownloads';
import {downloadFailureMessage, inAppMirror, orderBuilds, preselect, suitable, type HostPlatform} from '../../utils/updateDownloads';
import {loadPlatformCapabilities} from '../../utils/platformCapabilities';
import {useModalKeyboard} from '../../utils/modalKeyboard';

const downloads = useUpdateDownloads();
const game = downloads.selectedGame;
const builds = ref<UpdateBuild[]>([]);
const chosenBuild = ref<UpdateBuild | null>(null);
const chosenMirror = ref<UpdateMirror | null>(null);
const version = ref('');
const error = ref('');
const loading = ref(false);
const busy = ref(false);
const platform = ref<HostPlatform>('linux');
const modal = ref<HTMLElement | null>(null);
let generation = 0;
const job = computed(() => game.value ? [...downloads.jobs.value].reverse().find(j=>j.game_id===game.value?.id && !['done','cancelled'].includes(j.stage)) : undefined);
const ordered = computed(()=>orderBuilds(builds.value,platform.value));
const close = () => { generation++; if (game.value) void api.closeUpdateDownloadLinks(game.value.id); downloads.close(); };
useModalKeyboard(modal,()=>!!game.value,close);
async function load() {
  if (!game.value) return;
  const current = ++generation;
  loading.value = true; error.value = ''; builds.value = []; chosenBuild.value = null; chosenMirror.value = null;
  try {
    const [response, capabilities] = await Promise.all([api.getUpdateDownloadLinks(game.value.id),loadPlatformCapabilities()]);
    if (current !== generation) return;
    if (!response.success) throw new Error(response.error || 'Could not load links');
    platform.value = capabilities.platform === 'windows' ? 'windows' : 'linux';
    builds.value = response.builds || []; version.value = response.version || 'Unknown';
    const choice = preselect(builds.value,platform.value);
    chosenBuild.value = choice?.build || null; chosenMirror.value = choice?.mirror || null;
  } catch (e) { if (current === generation) error.value = String(e instanceof Error ? e.message : e); }
  finally { if (current === generation) loading.value = false; }
}
watch(game, current => { if (current) void load(); });
function choose(build: UpdateBuild, mirror: UpdateMirror) { chosenBuild.value=build; chosenMirror.value=mirror; }
async function start() {
  if (!game.value || !chosenMirror.value || busy.value) return;
  busy.value=true; error.value='';
  try {
    const response = await api.startUpdateDownload(game.value.id,chosenMirror.value.link_id);
    if (!response.success) throw new Error(response.error || 'Could not start download');
    downloads.refresh();
  } catch (e) { error.value=String(e instanceof Error ? e.message : e); }
  finally { busy.value=false; }
}
async function openBrowser() {
  if (!game.value || !chosenMirror.value) return;
  const response = await api.openUpdateDownloadInBrowser(game.value.id,chosenMirror.value.link_id);
  if (!response.success) error.value=response.error || 'Could not open browser';
}
const progressLabel = computed(()=>{
  if (!job.value) return '';
  if (job.value.stage === 'needs_user') return 'Finish the check in the browser window that opened.';
  if (job.value.stage === 'resolving') return 'Resolving link…';
  if (job.value.stage === 'verifying') return 'Verifying archive…';
  if (job.value.stage === 'failed') return downloadFailureMessage(job.value.error);
  const total = job.value.total ? ` / ${(job.value.total / 1048576).toFixed(1)} MiB` : '';
  return `Downloading ${(job.value.bytes / 1048576).toFixed(1)} MiB${total}`;
});
</script>
<template>
  <div v-if="game" class="fixed inset-0 z-[100] flex items-center justify-center p-4" style="background:rgba(0,0,0,.65)" @click.self="close">
    <section ref="modal" role="dialog" aria-modal="true" aria-labelledby="download-title" class="w-full max-w-2xl rounded-xl p-5 shadow-xl max-h-[85vh] overflow-auto" style="background:var(--bg-raised);color:var(--text-primary);border:1px solid var(--border)">
      <div class="flex items-start justify-between gap-3"><div><h2 id="download-title" class="text-lg font-semibold">Download update</h2><p>{{ game.title }} · {{ version }}</p></div><button class="ui-action-btn" @click="close" aria-label="Close">✕</button></div>
      <p v-if="loading" class="mt-4">Loading download links…</p>
      <div v-else-if="error && !builds.length" class="mt-4"><p>{{ downloadFailureMessage(error) }}</p><button class="ui-action-btn mt-2" @click="load">Retry</button></div>
      <template v-else>
        <p v-if="!builds.length" class="mt-4">No download links found.</p>
        <div v-for="build in ordered.suitable" :key="build.label + build.section" class="mt-4">
          <strong>{{ build.section ? build.section + ' · ' : '' }}{{ build.label }}</strong><span v-if="build.kind === 'patch'"> · Patch</span>
          <div class="flex flex-wrap gap-2 mt-2"><button v-for="mirror in build.mirrors" :key="mirror.link_id" class="ui-action-btn px-3 py-2 rounded" :style="{border: chosenMirror?.link_id === mirror.link_id ? '2px solid var(--brand)' : '1px solid var(--border)'}" @click="choose(build,mirror)">{{ mirror.label || mirror.host }}<span v-if="mirror.split"> · {{ mirror.part }} (not supported yet)</span></button></div>
        </div>
        <details v-if="ordered.other.length" class="mt-4"><summary>Other platforms</summary><div v-for="build in ordered.other" :key="build.label + build.section" class="mt-3"><strong>{{ build.label }}</strong><div class="flex flex-wrap gap-2"><button v-for="mirror in build.mirrors" :key="mirror.link_id" class="ui-action-btn px-3 py-2" @click="choose(build,mirror)">{{ mirror.label || mirror.host }}</button></div></div></details>
        <p v-if="chosenBuild?.kind === 'patch'" class="mt-3">This patch updates an existing install. Keep the old version when installing it.</p>
        <p v-if="chosenMirror?.host === 'buzzheavier'" class="mt-3">Enable Buzzheavier downloads in the extension settings.</p>
        <p v-if="error" role="alert" class="mt-3">{{ downloadFailureMessage(error) }}</p>
        <div v-if="job" class="mt-4"><p>{{ progressLabel }}</p><progress v-if="job.total && job.stage === 'downloading'" :value="job.bytes" :max="job.total" class="w-full" /><button v-if="job.stage !== 'failed'" class="ui-action-btn mt-2" @click="api.cancelUpdateDownload(job.job_id)">Cancel</button></div>
        <div v-if="!job || job.stage === 'failed'" class="flex gap-2 mt-4"><button v-if="chosenMirror && chosenBuild && suitable(chosenBuild,platform) && inAppMirror(chosenMirror)" class="ui-action-btn px-3 py-2" :disabled="busy" @click="start">Download</button><button v-if="chosenMirror" class="ui-action-btn px-3 py-2" @click="openBrowser">Open in browser</button></div>
      </template>
    </section>
  </div>
</template>
