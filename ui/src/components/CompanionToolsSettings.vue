<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import { IconAppWindow } from "@tabler/icons-vue";
import { api, type CompanionTool, type CompanionToolInput } from "../services/api";

const tools = ref<CompanionTool[]>([]);
const draft = ref<CompanionToolInput | null>(null);
const error = ref("");
const busy = ref(false);
const section = ref<HTMLElement | null>(null);
const edit = (tool?: CompanionTool) => {
  error.value = "";
  draft.value = tool ? { ...tool } : { name: "", kind: "wine", path: "", arguments: "", show_on_all_games: true, sort_order: tools.value.length };
};
const load = async () => {
  const result = await api.listCompanionTools();
  if (!result.success) throw new Error(result.error || "Could not load custom tools");
  tools.value = result.tools || [];
};
const save = async () => {
  if (!draft.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.saveCompanionTool({ ...draft.value });
    if (!result.success) throw new Error(result.error || "Could not save tool");
    draft.value = null;
    await load();
  } catch (cause) { error.value = String(cause instanceof Error ? cause.message : cause); }
  finally { busy.value = false; }
};
const remove = async (tool: CompanionTool) => {
  if (!confirm(`Delete '${tool.name}' and its per-game overrides?`)) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.deleteCompanionTool(tool.id);
    if (!result.success) throw new Error(result.error || "Could not delete tool");
    if (draft.value?.id === tool.id) draft.value = null;
    await load();
  } catch (cause) { error.value = String(cause instanceof Error ? cause.message : cause); }
  finally { busy.value = false; }
};
const browse = async () => {
  if (!draft.value) return;
  try {
    const path = await api.browseRunnerFile(draft.value.path);
    if (path && draft.value) draft.value.path = path;
  } catch (cause) { error.value = String(cause instanceof Error ? cause.message : cause); }
};
onMounted(async () => {
  try { await load(); } catch (cause) { error.value = String(cause); }
  await nextTick();
  if (window.location.hash.endsWith("#custom-tools")) section.value?.scrollIntoView({ block: "start" });
});
</script>

<template>
  <section id="custom-tools" ref="section" class="settings-card companion-settings rounded-xl p-6 space-y-4" aria-labelledby="custom-tools-heading">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 id="custom-tools-heading" class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconAppWindow class="ui-section-icon" />
          Custom tools
        </h3>
        <p class="help text-xs mt-1">Open helpers from a running game's right-click menu. Wine tools use the game's runner and prefix and close with it. Native and Command tools keep running.</p>
      </div>
      <button type="button" class="tool-btn shrink-0" :disabled="busy" @click="edit()">Add tool</button>
    </div>
    <p v-if="!tools.length" class="help text-sm">No custom tools yet.</p>
    <ul class="space-y-2">
      <li v-for="tool in tools" :key="tool.id" class="tool-row flex items-center justify-between gap-3 rounded-lg p-3">
        <div class="min-w-0">
          <p class="font-medium break-words">{{ tool.name }} <span class="help text-xs capitalize">{{ tool.kind }}</span></p>
          <p class="help text-xs truncate">{{ tool.kind === 'command' ? tool.arguments : tool.path }}</p>
          <p class="help text-xs">{{ tool.show_on_all_games ? 'Shown on all games by default' : 'Opt in per game' }}</p>
        </div>
        <div class="flex gap-2 shrink-0">
          <button type="button" class="tool-btn" :aria-label="`Edit ${tool.name}`" :disabled="busy" @click="edit(tool)">Edit</button>
          <button type="button" class="tool-btn" :aria-label="`Delete ${tool.name}`" :disabled="busy" @click="remove(tool)">Delete</button>
        </div>
      </li>
    </ul>
    <form v-if="draft" class="tool-row rounded-lg p-4 space-y-3" @submit.prevent="save">
      <h4 class="font-medium">{{ draft.id ? 'Edit tool' : 'Add tool' }}</h4>
      <fieldset :disabled="busy" class="space-y-3">
        <label class="block text-sm">Name<input v-model="draft.name" class="tool-input mt-1 w-full" required /></label>
        <label class="block text-sm">Kind
          <select v-model="draft.kind" class="tool-input mt-1 w-full"><option value="wine">Wine</option><option value="native">Native</option><option value="command">Command</option></select>
        </label>
        <div v-if="draft.kind !== 'command'">
          <label for="companion-tool-path" class="block text-sm mb-1">Executable path</label>
          <div class="flex gap-2"><input id="companion-tool-path" v-model="draft.path" class="tool-input min-w-0 flex-1" required /><button type="button" class="tool-btn" @click="browse">Browse</button></div>
        </div>
        <label class="block text-sm">{{ draft.kind === 'command' ? 'Command line' : 'Arguments (optional)' }}
          <input v-model="draft.arguments" class="tool-input mt-1 w-full font-mono" :required="draft.kind === 'command'" aria-describedby="companion-arguments-help" />
        </label>
        <p id="companion-arguments-help" class="help text-xs">Use %game_dir% and %game_exe% for the running game's folder and executable. Leading VAR=value entries set environment variables. Quote arguments with spaces; commands run directly without shell operators.</p>
        <label class="flex items-center justify-between gap-3 text-sm">Show on all games<input v-model="draft.show_on_all_games" type="checkbox" role="switch" /></label>
        <div class="flex gap-2"><button type="submit" class="tool-btn">{{ busy ? 'Saving…' : 'Save tool' }}</button><button type="button" class="tool-btn" @click="draft = null; error = ''">Cancel</button></div>
      </fieldset>
    </form>
    <p v-if="error" role="alert" class="text-sm" style="color: var(--danger-text)">{{ error }}</p>
  </section>
</template>

<style scoped>
.companion-settings { color: var(--text-primary); scroll-margin-top: 1rem; }
.help { color: var(--text-muted); }
.tool-row { background: var(--bg-raised); border: 1px solid var(--border); }
.tool-btn, .tool-input { border: 1px solid var(--border); border-radius: .5rem; background: var(--bg-surface); color: var(--text-primary); padding: .45rem .65rem; font-size: .875rem; }
.tool-btn:hover { border-color: var(--brand); }
.tool-btn:disabled { opacity: .5; }
input[type="checkbox"] { accent-color: var(--brand); }
</style>
