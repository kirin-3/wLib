<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { api, type GameRecord, type RunnerInfo } from "../services/api";
import { wineApplicable } from "../utils/bulkActions";
import { useModalKeyboard } from "../utils/modalKeyboard";
import PathStatus from "./PathStatus.vue";

export interface WineChoice {
  prefix: { mode: "keep" | "folder" | "default"; path: string };
  runner: { mode: "keep" | "set" | "global"; path: string };
}

const props = defineProps<{ modelValue: boolean; games: GameRecord[]; runningIds: ReadonlySet<number> }>();
const emit = defineEmits<{ (event: "update:modelValue", value: boolean): void; (event: "apply", choice: WineChoice): void }>();

const dialog = ref<HTMLElement | null>(null);
const prefixMode = ref<WineChoice["prefix"]["mode"]>("keep");
const prefixPath = ref("");
const runnerMode = ref<WineChoice["runner"]["mode"]>("keep");
const runnerPath = ref("");
const runners = ref<RunnerInfo[]>([]);
const loadingRunners = ref(false);

const close = () => emit("update:modelValue", false);
useModalKeyboard(dialog, () => props.modelValue, close);

const applicableCount = computed(() => props.games.filter((game) => wineApplicable(game, props.runningIds)).length);
const trimmedPath = computed(() => prefixPath.value.trim());
const pathValid = computed(() => trimmedPath.value.startsWith("/"));
const reason = computed(() => {
  if (prefixMode.value === "keep" && runnerMode.value === "keep") return "Choose a prefix or a runner to change";
  if (prefixMode.value === "folder" && !trimmedPath.value) return "Enter a prefix folder";
  if (prefixMode.value === "folder" && !pathValid.value) return "The prefix folder must be an absolute path";
  if (runnerMode.value === "set" && !runnerPath.value) return "Choose a runner";
  if (!applicableCount.value) return "No selected game uses Wine/Proton without a sandbox";
  return "";
});

watch(() => props.modelValue, async (open) => {
  if (!open) return;
  prefixMode.value = "keep"; prefixPath.value = ""; runnerMode.value = "keep"; runnerPath.value = "";
  await nextTick();
  dialog.value?.focus();
  if (runners.value.length || loadingRunners.value) return;
  loadingRunners.value = true;
  try {
    const res = await api.getAvailableRunners();
    if (res?.success) runners.value = res.runners || [];
  } catch (error) { console.error("Failed to get runners", error); }
  finally { loadingRunners.value = false; }
});

const browse = async () => {
  try {
    const picked = await api.browseDirectory(prefixPath.value || "");
    if (picked) prefixPath.value = picked;
  } catch (error) { console.error("Failed to browse directory", error); }
};
const apply = () => {
  if (reason.value) return;
  emit("apply", { prefix: { mode: prefixMode.value, path: trimmedPath.value }, runner: { mode: runnerMode.value, path: runnerPath.value } });
  close();
};
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="bulk-wine-title" tabindex="-1" class="bulk-wine w-full max-w-lg rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none">
        <div class="px-6 pt-6 pb-4 overflow-y-auto min-h-0 space-y-5">
          <div>
            <h3 id="bulk-wine-title" class="text-lg font-bold" style="color: var(--text-primary)">Set Wine/Proton</h3>
            <p class="mt-1 text-sm" style="color: var(--text-secondary)">{{ applicableCount }} of {{ games.length }} selected games can change. Native, custom, sandboxed and running games are skipped.</p>
          </div>

          <fieldset class="space-y-2">
            <legend class="wine-label">Wine prefix</legend>
            <label class="flex items-center gap-2 text-sm"><input v-model="prefixMode" type="radio" value="keep" /> Leave unchanged</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="prefixMode" type="radio" value="folder" /> Use this folder</label>
            <div v-if="prefixMode === 'folder'" class="pl-6 space-y-1">
              <div class="flex gap-2">
                <input v-model="prefixPath" type="text" aria-label="Prefix folder" placeholder="/home/user/games/shared_prefix" class="wine-input flex-1 font-mono text-xs" />
                <button type="button" class="ui-action-btn wine-browse px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" @click="browse">Browse</button>
              </div>
              <PathStatus :path="prefixPath" kind="dir" />
              <p class="text-xs" style="color: var(--text-muted)">Games sharing a prefix can only run one at a time. The first launch creates the prefix.</p>
            </div>
            <label class="flex items-center gap-2 text-sm"><input v-model="prefixMode" type="radio" value="default" /> Use the default prefix</label>
          </fieldset>

          <fieldset class="space-y-2">
            <legend class="wine-label">Runner</legend>
            <label class="flex items-center gap-2 text-sm"><input v-model="runnerMode" type="radio" value="keep" /> Leave unchanged</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="runnerMode" type="radio" value="set" /> Use this runner</label>
            <div v-if="runnerMode === 'set'" class="pl-6">
              <select v-model="runnerPath" aria-label="Runner" class="wine-input w-full text-sm">
                <option value="">{{ loadingRunners ? "Loading runners..." : "Choose a runner" }}</option>
                <option v-for="runner in runners" :key="runner.path" :value="runner.path">{{ runner.name }}</option>
              </select>
            </div>
            <label class="flex items-center gap-2 text-sm"><input v-model="runnerMode" type="radio" value="global" /> Use the global runner</label>
          </fieldset>
        </div>
        <div class="px-6 py-4 flex items-center justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <span v-if="reason" class="mr-auto text-xs" style="color: var(--text-muted)">{{ reason }}</span>
          <button class="px-5 py-2.5 rounded-lg text-sm font-medium" style="color: var(--text-secondary)" @click="close">Cancel</button>
          <button class="px-6 py-2.5 rounded-lg text-sm font-bold" style="background: var(--brand); color: var(--on-brand)" :disabled="!!reason" :style="reason ? 'opacity:.5;cursor:not-allowed' : ''" :title="reason" @click="apply">Apply</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.wine-label { display: block; margin-bottom: 0.4rem; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-muted); }
.wine-input { background: var(--bg-inset); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.5rem 0.75rem; color: var(--text-primary); }
.wine-input:focus-visible, .wine-browse:focus-visible, input[type="radio"]:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.wine-browse { color: var(--text-primary); border: 1px solid var(--border); }
input[type="radio"] { accent-color: var(--brand); }
.bulk-wine { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
</style>
