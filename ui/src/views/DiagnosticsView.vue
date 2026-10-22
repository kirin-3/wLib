<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { api, onWebviewReady, type DiagnosticsBasic, type DiagnosticsTools } from "../services/api";
import { copyText } from "../utils/copyText";
import { notify, notifyError } from "../utils/toast";

const basic = ref<DiagnosticsBasic | null>(null);
const tools = ref<DiagnosticsTools | null>(null);
const basicLoading = ref(true);
const toolsLoading = ref(true);
const basicError = ref("");
const toolsError = ref("");
const copying = ref(false);
const opening = ref(false);
const manualReport = ref("");
const reportBox = ref<HTMLTextAreaElement | null>(null);
let request = 0;
const busy = computed(() => basicLoading.value || toolsLoading.value);
const unavailable = (value: unknown): string => value == null || value === "" ? "Not available"
  : typeof value === "object" ? JSON.stringify(value) : String(value);
const rendererRows = computed(() => {
  const rows: [string, string][] = [["Qt", unavailable(basic.value?.qt)], ["WebEngine", unavailable(basic.value?.webengine)]];
  for (const stage of ["startup", "runtime"]) {
    const values = basic.value?.renderer_stages[stage];
    if (!values || !Object.keys(values).length) rows.push([`${stage === "startup" ? "Startup" : "Runtime"} probe`, "Not available"]);
    else for (const [key, value] of Object.entries(values)) rows.push([`${stage} / ${key}`, unavailable(value)]);
  }
  return rows;
});
const toolRows = computed(() => {
  const value = (name: "7zz" | "7z" | "7za" | "bsdtar" | "unrar" | "wine") => toolsLoading.value ? "Checking…"
    : toolsError.value ? "Not available" : tools.value?.[name] || "Not found";
  const rows: [string, string][] = [["7-Zip (7zz)", value("7zz")], ["7-Zip (7z)", value("7z")], ["7-Zip (7za)", value("7za")], ["bsdtar", value("bsdtar")], ["unrar", value("unrar")]];
  if (basic.value?.platform.startsWith("linux") || tools.value?.runners !== undefined) {
    rows.push(["Wine", value("wine")]);
    if (toolsLoading.value) rows.push(["Wine / Proton runners", "Checking…"]);
    else if (!tools.value?.runners?.length) rows.push(["Wine / Proton runners", toolsError.value ? "Not available" : "Not found"]);
    else for (const runner of tools.value.runners) rows.push([runner.name, runner.path]);
  }
  return rows;
});

async function refresh() {
  const id = ++request;
  basicLoading.value = toolsLoading.value = true;
  basicError.value = toolsError.value = "";
  manualReport.value = "";
  await Promise.allSettled([
    (async () => {
      try {
        const result = await api.getDiagnostics();
        if (!result.success || !result.basic) throw new Error(result.error || "Could not load app details");
        if (id === request) basic.value = result.basic;
      } catch (error) { if (id === request) { basic.value = null; basicError.value = unavailable(error instanceof Error ? error.message : error); } }
      finally { if (id === request) basicLoading.value = false; }
    })(),
    (async () => {
      try {
        const result = await api.getDiagnosticsTools();
        if (!result.success || !result.tools) throw new Error(result.error || "Could not check tools");
        if (id === request) tools.value = result.tools;
      } catch (error) { if (id === request) { tools.value = null; toolsError.value = unavailable(error instanceof Error ? error.message : error); } }
      finally { if (id === request) toolsLoading.value = false; }
    })(),
  ]);
}
async function copyReport() {
  copying.value = true;
  try {
    const result = await api.getDiagnosticsReport();
    if (!result.success || !result.report) throw new Error(result.error || "Could not build report");
    if (await copyText(result.report)) {
      manualReport.value = "";
      notify("Diagnostics report copied.", "success");
    } else {
      manualReport.value = result.report;
      await nextTick();
      reportBox.value?.focus();
      reportBox.value?.select();
    }
  } catch (error) { notifyError(error instanceof Error ? error.message : String(error), "Could not copy report"); }
  finally { copying.value = false; }
}
async function openLogs() {
  opening.value = true;
  try {
    const result = await api.openLogFolder();
    if (!result.success) throw new Error(result.error || "Could not open log folder");
  } catch (error) { notifyError(error instanceof Error ? error.message : String(error), "Could not open log folder"); }
  finally { opening.value = false; }
}
onMounted(() => onWebviewReady(() => { void refresh(); }));
onBeforeUnmount(() => { request++; });
</script>

<template>
  <div class="diagnostics-page allow-text-selection">
    <header>
      <RouterLink to="/settings" class="back-link">Back to Settings</RouterLink>
      <h1>Diagnostics</h1>
      <p>App details for a bug report. Copied reports replace your home folder with ~ and hide your login name in other paths.</p>
      <div class="diagnostics-actions">
        <button type="button" class="primary-action" :disabled="copying || busy || !!basicError" @click="copyReport">{{ copying ? 'Copying…' : 'Copy report' }}</button>
        <button type="button" :disabled="opening" @click="openLogs">{{ opening ? 'Opening…' : 'Open log folder' }}</button>
        <button type="button" :disabled="busy" @click="refresh">{{ busy ? 'Refreshing…' : 'Refresh' }}</button>
      </div>
    </header>
    <p v-if="basicLoading" role="status">Loading app details…</p>
    <p v-if="basicError" role="alert">{{ basicError }}. Use Refresh to try again.</p>
    <template v-if="basic">
      <section aria-labelledby="diagnostics-app">
        <h2 id="diagnostics-app">App</h2>
        <dl>
          <div><dt>wLib version</dt><dd>{{ unavailable(basic.app_version) }}</dd></div>
          <div><dt>Extension version</dt><dd>{{ unavailable(basic.extension_version) }}</dd></div>
          <div><dt>Packaging</dt><dd>{{ unavailable(basic.packaging) }}</dd></div>
          <div><dt>Operating system</dt><dd>{{ unavailable(basic.os) }}</dd></div>
          <div><dt>Python</dt><dd>{{ unavailable(basic.python) }}</dd></div>
        </dl>
      </section>
      <section aria-labelledby="diagnostics-renderer">
        <h2 id="diagnostics-renderer">Renderer</h2>
        <dl><div v-for="[label, value] in rendererRows" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
      </section>
      <section aria-labelledby="diagnostics-storage">
        <h2 id="diagnostics-storage">Storage and library</h2>
        <dl>
          <div><dt>Data folder</dt><dd>{{ basic.data_path }}</dd></div>
          <div><dt>Database file</dt><dd>{{ basic.database_path }}</dd></div>
          <div><dt>Database size (including WAL)</dt><dd>{{ basic.database_bytes.toLocaleString() }} bytes</dd></div>
          <div><dt>Log folder</dt><dd>{{ basic.log_path }}</dd></div>
          <div><dt>Games</dt><dd>{{ basic.game_count }}</dd></div>
        </dl>
      </section>
    </template>
    <section aria-labelledby="diagnostics-extension">
      <h2 id="diagnostics-extension">Extension</h2>
      <dl><div><dt>Local server (127.0.0.1:8183)</dt><dd>{{ toolsLoading ? 'Checking…' : toolsError ? 'Not available' : tools?.extension.reachable ? 'Reachable' : 'Not reachable' }}</dd></div>
        <div v-if="tools?.extension.error"><dt>Server response</dt><dd>{{ tools.extension.error }}</dd></div>
      </dl>
    </section>
    <section aria-labelledby="diagnostics-tools" :aria-busy="toolsLoading">
      <h2 id="diagnostics-tools">Tools</h2>
      <p v-if="toolsError" role="alert">{{ toolsError }}. Use Refresh to try again.</p>
      <dl><div v-for="[label, value] in toolRows" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
    </section>
    <section v-if="manualReport" aria-labelledby="diagnostics-manual">
      <h2 id="diagnostics-manual">Copy report manually</h2>
      <p>Clipboard access was unavailable. The report is selected below; copy it with your keyboard or context menu.</p>
      <textarea ref="reportBox" :value="manualReport" readonly aria-label="Diagnostics report" rows="14" />
    </section>
  </div>
</template>

<style scoped>
.diagnostics-page { width: 100%; max-width: 64rem; min-width: 0; padding: 1rem; color: var(--text-primary); overflow-wrap: anywhere; }
header { margin-bottom: 2rem; }
.back-link { display: inline-flex; align-items: center; min-height: 44px; color: var(--text-secondary); text-decoration: underline; text-underline-offset: 3px; }
h1 { font-size: clamp(1.5rem, 3vw, 1.875rem); font-weight: 700; margin: 0.5rem 0; }
p { color: var(--text-secondary); font-size: 0.875rem; line-height: 1.6; }
.diagnostics-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1.25rem; }
button { min-width: 0; max-width: 100%; min-height: 44px; padding: 0.5rem 1rem; border: 1px solid var(--text-secondary); border-radius: 0.5rem; background: var(--bg-raised); color: var(--text-primary); font-size: 0.875rem; font-weight: 600; cursor: pointer; }
button:hover:enabled { background: var(--bg-overlay); }
button.primary-action { background: var(--brand); color: var(--on-brand); border-color: var(--brand); }
button.primary-action:hover:enabled { filter: brightness(1.08); background: var(--brand); }
button:disabled { cursor: wait; opacity: 0.6; }
button:focus-visible, a:focus-visible, textarea:focus-visible { outline: 2px solid var(--text-primary); outline-offset: 3px; }
section { margin-top: 2rem; }
h2 { font-size: 1.125rem; font-weight: 600; margin-bottom: 0.75rem; }
dl { border-top: 1px solid var(--border); }
dl > div { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.25rem; padding: 0.75rem 0; border-bottom: 1px solid var(--border); font-size: 0.875rem; }
dt { color: var(--text-secondary); }
dd { min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
textarea { width: 100%; margin-top: 1rem; padding: 0.75rem; border: 1px solid var(--text-secondary); border-radius: 0.5rem; background: var(--bg-inset); color: var(--text-primary); font-family: inherit; font-size: 0.875rem; overflow-wrap: anywhere; }
@media (min-width: 40rem) { .diagnostics-page { padding: 1.5rem; } dl > div { grid-template-columns: minmax(12rem, 35%) minmax(0, 1fr); gap: 1rem; } }
@media (min-width: 64rem) { .diagnostics-page { padding: 2rem; } }
</style>
