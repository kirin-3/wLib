<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref, watch } from "vue";
import { api, type GameCompanionTool } from "../services/api";
const props = defineProps<{ gameId: number }>();
const emit = defineEmits<{ settings: [] }>();
const tools = ref<GameCompanionTool[]>([]);
const error = ref("");
const loading = ref(true);
const saving = ref(false);
let requestId = 0;
const load = async () => {
  const request = ++requestId;
  loading.value = true;
  error.value = "";
  try {
    const result = await api.getGameCompanionTools(props.gameId);
    if (request !== requestId) return;
    if (!result.success) throw new Error(result.error || "Could not load custom tools");
    tools.value = result.tools || [];
  } catch (cause) { if (request === requestId) error.value = String(cause instanceof Error ? cause.message : cause); }
  finally { if (request === requestId) loading.value = false; }
};
const setOverride = async (tool: GameCompanionTool, visible: boolean | null) => {
  saving.value = true;
  error.value = "";
  try {
    const result = await api.setGameCompanionToolOverride(props.gameId, tool.id, visible);
    if (!result.success) throw new Error(result.error || "Could not save tool visibility");
    await load();
  } catch (cause) { error.value = String(cause instanceof Error ? cause.message : cause); }
  finally { saving.value = false; }
};
watch(() => props.gameId, () => { tools.value = []; void load(); }, { immediate: true });
</script>
<template>
  <div class="space-y-2" aria-labelledby="game-custom-tools-heading">
    <h3 id="game-custom-tools-heading" class="text-sm font-medium">Custom tools</h3>
    <p v-if="loading" class="help text-xs">Loading tools…</p>
    <p v-else-if="!tools.length && !error" class="help text-sm">No custom tools yet. <button type="button" class="underline" @click="emit('settings')">Open Settings &gt; Custom tools</button></p>
    <fieldset :disabled="saving || loading" class="space-y-2">
      <div v-for="tool in tools" :key="tool.id" class="flex items-center gap-3 text-sm">
        <label class="flex flex-1 items-center gap-2"><input type="checkbox" :checked="tool.visible" @change="setOverride(tool, ($event.target as HTMLInputElement).checked)" />{{ tool.name }}</label>
        <span v-if="!tool.overridden" class="help text-xs">default</span>
        <button v-else type="button" class="underline text-xs" :aria-label="`Reset ${tool.name} to default`" @click="setOverride(tool, null)">Reset</button>
      </div>
    </fieldset>
    <p v-if="error" role="alert" class="text-sm" style="color: var(--danger-text)">{{ error }}</p>
  </div>
</template>
<style scoped>
.help { color: var(--text-muted); }
input[type="checkbox"] { accent-color: var(--brand); }
</style>
