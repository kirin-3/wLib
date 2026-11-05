<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { IconAlertTriangle, IconAppWindow, IconArrowLeft, IconClipboardText, IconCopy, IconCpu, IconDatabase, IconFolderOpen, IconLoader2, IconPuzzle, IconRefresh, IconStethoscope, IconTools } from "@tabler/icons-vue";
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

const seconds = (value: number | null | undefined): string => value == null ? "Never" : `${value.toLocaleString()} s`;
const bridgeRows = computed(() => {
  if (toolsLoading.value) return [["Browser extension", "Checking…"]] as [string, string][];
  const bridge = tools.value?.extension.bridge;
  if (toolsError.value || !bridge) return [["Browser extension", "Not available"]] as [string, string][];
  const rows: [string, string][] = [[
    "Browser extension",
    bridge.connected ? `Connected${bridge.extension_version ? ` (v${bridge.extension_version})` : ""} for ${seconds(bridge.connected_seconds)}` : "Not connected",
  ]];
  if (bridge.connected) rows.push(["Can fetch F95Zone", bridge.can_fetch ? "Yes" : "No (f95zone.to permission missing)"]);
  rows.push(["Disconnects since start", bridge.disconnects.toLocaleString()]);
  if (bridge.disconnects) rows.push(["Last connection lasted", seconds(bridge.last_connection_seconds)]);
  const fetch = bridge.last_fetch;
  rows.push(["Last F95Zone fetch", !fetch ? "None yet"
    : fetch.error ? `Failed ${seconds(fetch.seconds_ago)} ago: ${fetch.error}`
    : `${fetch.signed_in === true ? "Logged in" : fetch.signed_in === false ? "Not logged in" : `HTTP ${fetch.status ?? "?"}`}, ${seconds(fetch.seconds_ago)} ago`]);
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
  <div class="diagnostics-page allow-text-selection mx-auto w-full p-8 max-w-5xl pb-12">
    <header class="mb-8">
      <RouterLink to="/settings" class="back-link"><IconArrowLeft class="w-4 h-4" />Back to Settings</RouterLink>
      <h2 class="ui-page-heading text-3xl font-bold mb-2 tracking-tight" style="color: var(--text-primary)">
        <IconStethoscope class="ui-page-heading-icon" />
        <span>Diagnostics</span>
      </h2>
      <p class="text-sm pl-3" style="color: var(--text-secondary); border-left: 2px solid var(--brand)">
        App details for a bug report. Copied reports replace your home folder with ~ and hide your login name in other paths.
      </p>
      <div class="diagnostics-actions">
        <button type="button" class="diag-btn primary-action" :disabled="copying || busy || !!basicError" @click="copyReport">
          <IconLoader2 v-if="copying" class="w-4 h-4 animate-spin" /><IconCopy v-else class="w-4 h-4" />{{ copying ? 'Copying…' : 'Copy report' }}
        </button>
        <button type="button" class="diag-btn" :disabled="opening" @click="openLogs"><IconFolderOpen class="w-4 h-4" />{{ opening ? 'Opening…' : 'Open log folder' }}</button>
        <button type="button" class="diag-btn" :disabled="busy" @click="refresh"><IconRefresh class="w-4 h-4" :class="{ 'animate-spin': busy }" />{{ busy ? 'Refreshing…' : 'Refresh' }}</button>
      </div>
    </header>
    <p v-if="basicLoading" role="status" class="flex items-center gap-2 text-sm" style="color: var(--text-muted)"><IconLoader2 class="w-4 h-4 animate-spin" />Loading app details…</p>
    <p v-if="basicError" role="alert" class="ui-callout ui-callout--danger"><IconAlertTriangle class="w-4 h-4 shrink-0" />{{ basicError }}. Use Refresh to try again.</p>
    <div class="space-y-6">
      <template v-if="basic">
        <section class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-app">
          <h3 id="diagnostics-app" class="ui-section-heading text-lg font-semibold"><IconAppWindow class="ui-section-icon" />App</h3>
          <dl>
            <div><dt>wLib version</dt><dd>{{ unavailable(basic.app_version) }}</dd></div>
            <div><dt>Extension version</dt><dd>{{ unavailable(basic.extension_version) }}</dd></div>
            <div><dt>Packaging</dt><dd>{{ unavailable(basic.packaging) }}</dd></div>
            <div><dt>Operating system</dt><dd>{{ unavailable(basic.os) }}</dd></div>
            <div><dt>Python</dt><dd>{{ unavailable(basic.python) }}</dd></div>
          </dl>
        </section>
        <section class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-renderer">
          <h3 id="diagnostics-renderer" class="ui-section-heading text-lg font-semibold"><IconCpu class="ui-section-icon" />Renderer</h3>
          <dl><div v-for="[label, value] in rendererRows" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
        </section>
        <section class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-storage">
          <h3 id="diagnostics-storage" class="ui-section-heading text-lg font-semibold"><IconDatabase class="ui-section-icon" />Storage and library</h3>
          <dl>
            <div><dt>Data folder</dt><dd class="mono">{{ basic.data_path }}</dd></div>
            <div><dt>Database file</dt><dd class="mono">{{ basic.database_path }}</dd></div>
            <div><dt>Database size (including WAL)</dt><dd>{{ basic.database_bytes.toLocaleString() }} bytes</dd></div>
            <div><dt>Log folder</dt><dd class="mono">{{ basic.log_path }}</dd></div>
            <div><dt>Games</dt><dd>{{ basic.game_count }}</dd></div>
          </dl>
        </section>
      </template>
      <section class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-extension">
        <h3 id="diagnostics-extension" class="ui-section-heading text-lg font-semibold"><IconPuzzle class="ui-section-icon" />Extension</h3>
        <dl><div><dt>Local server (127.0.0.1:8183)</dt><dd>{{ toolsLoading ? 'Checking…' : toolsError ? 'Not available' : tools?.extension.reachable ? 'Reachable' : 'Not reachable' }}</dd></div>
          <div v-if="tools?.extension.error"><dt>Server response</dt><dd>{{ tools.extension.error }}</dd></div>
          <div v-for="[label, value] in bridgeRows" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div>
        </dl>
      </section>
      <section class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-tools" :aria-busy="toolsLoading">
        <h3 id="diagnostics-tools" class="ui-section-heading text-lg font-semibold"><IconTools class="ui-section-icon" />Tools</h3>
        <p v-if="toolsError" role="alert" class="ui-callout ui-callout--danger mb-3"><IconAlertTriangle class="w-4 h-4 shrink-0" />{{ toolsError }}. Use Refresh to try again.</p>
        <dl><div v-for="[label, value] in toolRows" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
      </section>
      <section v-if="manualReport" class="diag-card rounded-xl p-6" aria-labelledby="diagnostics-manual">
        <h3 id="diagnostics-manual" class="ui-section-heading text-lg font-semibold"><IconClipboardText class="ui-section-icon" />Copy report manually</h3>
        <p class="text-sm mt-2" style="color: var(--text-secondary)">Clipboard access was unavailable. The report is selected below; copy it with your keyboard or context menu.</p>
        <textarea ref="reportBox" :value="manualReport" readonly aria-label="Diagnostics report" rows="14" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.diagnostics-page { color: var(--text-primary); min-width: 0; overflow-wrap: anywhere; }
.back-link { display: inline-flex; align-items: center; gap: 0.375rem; min-height: 2.25rem; margin-bottom: 0.75rem; font-size: 0.8125rem; color: var(--text-muted); }
.back-link:hover { color: var(--text-primary); }
.diagnostics-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 1.25rem; }
.diag-btn { display: inline-flex; align-items: center; gap: 0.5rem; max-width: 100%; min-height: 2.5rem; padding: 0.5rem 1rem; border: 1px solid var(--border-hover); border-radius: 0.5rem; background: var(--bg-overlay); color: var(--text-primary); font-size: 0.875rem; font-weight: 600; transition: background-color var(--motion-fast) ease, filter var(--motion-fast) ease; }
.diag-btn:hover:enabled { background: var(--border-hover); }
.diag-btn.primary-action { background: var(--brand); color: var(--on-brand); border-color: var(--brand); box-shadow: var(--shadow-brand); }
.diag-btn.primary-action:hover:enabled { filter: brightness(1.08); background: var(--brand); }
.diag-btn:disabled { cursor: wait; opacity: 0.6; }
.diag-btn:focus-visible, a:focus-visible, textarea:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--brand-glow); }
.diag-card { background: var(--bg-surface); border: 1px solid var(--border); }
dl { margin-top: 1rem; border-radius: 0.5rem; border: 1px solid var(--border); background: var(--bg-raised); overflow: hidden; }
dl > div { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.25rem; padding: 0.65rem 1rem; font-size: 0.8125rem; }
dl > div + div { border-top: 1px solid var(--border); }
dt { color: var(--text-muted); }
dd { min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; color: var(--text-primary); }
dd.mono { font-family: ui-monospace, monospace; font-size: 0.75rem; }
textarea { width: 100%; margin-top: 1rem; padding: 0.75rem; border: 1px solid var(--border); border-radius: 0.5rem; background: var(--bg-inset); color: var(--text-primary); font-family: ui-monospace, monospace; font-size: 0.75rem; overflow-wrap: anywhere; }
@media (min-width: 40rem) { dl > div { grid-template-columns: minmax(12rem, 35%) minmax(0, 1fr); gap: 1rem; } }
@media (max-width: 640px) { .diagnostics-page { padding: 1rem; } }
</style>
