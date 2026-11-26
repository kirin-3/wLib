<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { confirmAction } from "../utils/confirm";
import { onMounted, ref } from "vue";
import { api, type CompanionTool, type CompanionToolInput } from "../services/api";
import SettingsGroup from "./settings/SettingsGroup.vue";
import SettingsHelpLink from "./settings/SettingsHelpLink.vue";

const settingsRowVisible = useSettingsRowVisibility();

const tools = ref<CompanionTool[]>([]);
const draft = ref<CompanionToolInput | null>(null);
const error = ref("");
const busy = ref(false);
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
  if (!(await confirmAction({ title: 'Delete companion tool', message: `Delete '${tool.name}' and its per-game overrides?`, confirmLabel: 'Delete', danger: true }))) return;
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
});
</script>

<template>
  <SettingsGroup id="custom-tools" title="Custom tools" class="companion-settings"
    description="Open helpers from a running game's right-click menu. Wine tools use the game's runner and prefix and close with it. Native and Command tools keep running.">
    <template #actions>
      <span class="flex items-center gap-3 shrink-0">
        <SettingsHelpLink page="Launching-Games" anchor="custom-tools" />
        <button type="button" class="ui-card-btn shrink-0" :disabled="busy" @click="edit()">Add tool</button>
      </span>
    </template>
    <p data-settings-row="custom-tools-static-0" data-settings-bound v-show="settingsRowVisible('custom-tools-static-0')" v-if="!tools.length" class="help text-xs">No custom tools yet.</p>
    <ul data-settings-row="custom-tools-static-1" data-settings-bound v-show="settingsRowVisible('custom-tools-static-1')" v-if="tools.length" class="space-y-2">
      <li v-for="tool in tools" :key="tool.id" class="ui-card-row flex items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="font-medium break-words">{{ tool.name }} <span class="help text-xs capitalize">{{ tool.kind }}</span></p>
          <p class="help text-xs truncate">{{ tool.kind === 'command' ? tool.arguments : tool.path }}</p>
          <p class="help text-xs">{{ tool.show_on_all_games ? 'Shown on all games by default' : 'Opt in per game' }}</p>
        </div>
        <div class="flex gap-2 shrink-0">
          <button type="button" class="ui-card-btn" :aria-label="`Edit ${tool.name}`" :disabled="busy" @click="edit(tool)">Edit</button>
          <button type="button" class="ui-card-btn ui-card-btn--danger" :aria-label="`Delete ${tool.name}`" :disabled="busy" @click="remove(tool)">Delete</button>
        </div>
      </li>
    </ul>
    <form data-settings-row="custom-tools-static-2" data-settings-bound v-show="settingsRowVisible('custom-tools-static-2')" v-if="draft" class="ui-card-row space-y-3" @submit.prevent="save">
      <h5 class="text-sm font-medium">{{ draft.id ? 'Edit tool' : 'Add tool' }}</h5>
      <fieldset :disabled="busy" class="space-y-3">
        <label class="settings-field-label">Name<input v-model="draft.name" class="settings-input mt-1 w-full" required /></label>
        <label class="settings-field-label">Kind
          <select v-model="draft.kind" class="settings-input mt-1 w-full"><option value="wine">Wine</option><option value="native">Native</option><option value="command">Command</option></select>
        </label>
        <div v-if="draft.kind !== 'command'">
          <label for="companion-tool-path" class="settings-field-label mb-1">Executable path</label>
          <div class="flex gap-2"><input id="companion-tool-path" v-model="draft.path" class="settings-input min-w-0 flex-1" required /><button type="button" class="ui-card-btn" @click="browse">Browse</button></div>
        </div>
        <label class="settings-field-label">{{ draft.kind === 'command' ? 'Command line' : 'Arguments (optional)' }}
          <input v-model="draft.arguments" class="settings-input mt-1 w-full font-mono" :required="draft.kind === 'command'" aria-describedby="companion-arguments-help" />
        </label>
        <p id="companion-arguments-help" class="help text-xs">Use %game_dir% and %game_exe% for the running game's folder and executable. Leading VAR=value entries set environment variables. Quote arguments with spaces; commands run directly without shell operators.</p>
        <label class="flex items-center justify-between gap-3 text-sm cursor-pointer">Show on all games<span class="relative inline-flex shrink-0"><input v-model="draft.show_on_all_games" type="checkbox" role="switch" class="sr-only peer" /><span class="ui-toggle"></span></span></label>
        <div class="flex gap-2"><button type="submit" class="ui-card-btn ui-card-btn--primary">{{ busy ? 'Saving…' : 'Save tool' }}</button><button type="button" class="ui-card-btn" @click="draft = null; error = ''">Cancel</button></div>
      </fieldset>
    </form>
    <p data-settings-row="custom-tools-static-3" data-settings-bound v-show="settingsRowVisible('custom-tools-static-3')" v-if="error" role="alert" class="text-sm" style="color: var(--danger-text)">{{ error }}</p>
  </SettingsGroup>
</template>

<style scoped>
.companion-settings { color: var(--text-primary); }
.help { color: var(--text-muted); }
</style>
