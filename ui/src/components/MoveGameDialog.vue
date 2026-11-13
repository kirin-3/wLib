<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { IconFolderShare } from "@tabler/icons-vue";
import { api, type GameFilePlanEntry, type GameRecord, type LibraryFolder } from "../services/api";
import { actionableEntries, confirmReason, formatBytes, planProblem } from "../utils/gameFiles";
import { useModalKeyboard } from "../utils/modalKeyboard";
import { notifyError } from "../utils/toast";

const props = defineProps<{ modelValue: boolean; games: GameRecord[] }>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "started", queued: number): void;
}>();

const OTHER = "__other__";
const dialog = ref<HTMLElement | null>(null);
const folders = ref<LibraryFolder[]>([]);
const choice = ref("");
const otherFolder = ref("");
const entries = ref<GameFilePlanEntry[]>([]);
const overrides = ref<Record<string, string>>({});
const loading = ref(false);
const starting = ref(false);
let request = 0;

const close = () => emit("update:modelValue", false);
useModalKeyboard(dialog, () => props.modelValue, close);

const destination = computed(() => (choice.value === OTHER ? otherFolder.value : choice.value));
const single = computed(() => props.games.length === 1);
const goingCount = computed(() => actionableEntries(entries.value).length);
const reason = computed(() => {
  if (!destination.value) return "Choose a destination folder.";
  return confirmReason(entries.value, loading.value, "move");
});

const plan = async () => {
  const current = ++request;
  if (!destination.value) { entries.value = []; return; }
  loading.value = true;
  try {
    const res = await api.planGameMove(props.games.map((game) => game.id), destination.value, overrides.value);
    if (current !== request) return;
    if (!res.success) notifyError(res.error || "Could not check the move.");
    entries.value = res.games ?? [];
  } catch (error) {
    if (current === request) notifyError(String(error));
  } finally {
    if (current === request) loading.value = false;
  }
};

watch(() => props.modelValue, async (open) => {
  if (!open) return;
  request++;
  entries.value = [];
  overrides.value = {};
  choice.value = "";
  otherFolder.value = "";
  starting.value = false;
  await nextTick();
  dialog.value?.focus();
  try { folders.value = (await api.listLibraryFolders()).filter((folder) => folder.available); }
  catch (error) { console.error("Failed to list library folders", error); }
});

const pickOther = async () => {
  let picked = "";
  try { picked = await api.browseDirectory(otherFolder.value); }
  catch (error) { console.error("Failed to browse directory", error); }
  if (picked) otherFolder.value = picked;
  else if (!otherFolder.value) choice.value = "";
  void plan();
};

const onChoice = () => {
  if (choice.value === OTHER) void pickOther();
  else void plan();
};

const changeRoot = async (entry: GameFilePlanEntry) => {
  let picked = "";
  try { picked = await api.browseDirectory(entry.root); }
  catch (error) { console.error("Failed to browse directory", error); }
  if (!picked) return;
  overrides.value = { ...overrides.value, [String(entry.game_id)]: picked };
  void plan();
};

const start = async () => {
  if (reason.value || starting.value) return;
  starting.value = true;
  const ids = actionableEntries(entries.value).map((entry) => entry.game_id);
  const rootOverrides = Object.fromEntries(Object.entries(overrides.value).filter(([id]) => ids.includes(Number(id))));
  try {
    const res = await api.startGameMove(ids, destination.value, rootOverrides);
    if (!res.success) { notifyError(res.error || "Could not start the move."); return; }
    emit("started", res.queued?.length ?? 0);
    close();
  } catch (error) {
    notifyError(String(error));
  } finally {
    starting.value = false;
  }
};
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="move-title" tabindex="-1" class="files-dialog w-full max-w-xl rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none">
        <div class="px-6 pt-6 pb-4 overflow-y-auto min-h-0 space-y-4">
          <div class="flex gap-4">
            <span class="files-icon" aria-hidden="true"><IconFolderShare class="w-5 h-5" /></span>
            <div class="min-w-0 pt-1">
              <h3 id="move-title" class="text-lg font-bold" style="color: var(--text-primary)">{{ single ? `Move ${games[0]?.title ?? ""}` : `Move ${games.length} games` }}</h3>
              <p class="mt-1 text-sm" style="color: var(--text-secondary)">Each game folder keeps its name under the new folder. Moves run in the background, one game at a time.</p>
            </div>
          </div>

          <div>
            <label for="move-destination" class="files-label">Move to</label>
            <select id="move-destination" v-model="choice" class="files-input w-full text-sm" @change="onChoice">
              <option value="">Choose a folder</option>
              <option v-for="folder in folders" :key="folder.path" :value="folder.path">{{ folder.path }}{{ folder.free_bytes !== null ? ` (${formatBytes(folder.free_bytes)} free)` : "" }}</option>
              <option :value="OTHER">{{ otherFolder ? `Other folder: ${otherFolder}` : "Other folder..." }}</option>
            </select>
            <button v-if="choice === OTHER" type="button" class="ui-action-btn files-btn mt-2 px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" @click="pickOther">Browse...</button>
          </div>

          <p v-if="loading" class="text-sm" style="color: var(--text-muted)">Checking the game folders...</p>
          <ul v-else-if="entries.length" class="space-y-2" aria-label="Games to move">
            <li v-for="entry in entries" :key="entry.game_id" class="files-row rounded-lg p-3">
              <div class="flex items-start gap-2">
                <div class="min-w-0 flex-1">
                  <div class="flex items-baseline gap-2">
                    <span class="text-sm font-semibold truncate" style="color: var(--text-primary)">{{ entry.title }}</span>
                    <span v-if="entry.size_bytes !== null && entry.size_bytes !== undefined" class="text-xs shrink-0" style="color: var(--text-muted)">{{ formatBytes(entry.size_bytes) }}</span>
                  </div>
                  <div v-if="entry.root" class="files-path font-mono text-xs break-all">{{ entry.root }}</div>
                  <div v-if="entry.target && !planProblem(entry)" class="mt-1 text-xs break-all" style="color: var(--text-muted)">To {{ entry.target }}{{ entry.same_device ? " (same drive)" : " (copied to another drive)" }}</div>
                  <div v-if="planProblem(entry)" class="files-reason mt-1 text-xs" role="note">{{ planProblem(entry) }}</div>
                </div>
                <button v-if="entry.root && !entry.blocked_reason" type="button" class="ui-action-btn files-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface shrink-0" :aria-label="`Change the folder of ${entry.title}`" @click="changeRoot(entry)">Change...</button>
              </div>
            </li>
          </ul>
        </div>
        <div class="px-6 py-4 flex items-center justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <span v-if="reason && !loading" class="mr-auto text-xs" style="color: var(--text-muted)">{{ reason }}</span>
          <span v-else-if="!single && goingCount < entries.length" class="mr-auto text-xs" style="color: var(--text-muted)">{{ entries.length - goingCount }} skipped</span>
          <button class="files-cancel px-5 py-2.5 rounded-lg text-sm font-medium" @click="close">Cancel</button>
          <button class="files-primary px-6 py-2.5 rounded-lg text-sm font-bold" :disabled="!!reason || starting" :title="reason" @click="start">{{ single || goingCount === 1 ? "Move" : `Move ${goingCount}` }}</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.files-dialog { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.files-icon { display: inline-flex; align-items: center; justify-content: center; width: 2.5rem; height: 2.5rem; flex-shrink: 0; border-radius: 9999px; background: var(--brand-glow); color: var(--brand); }
.files-label { display: block; margin-bottom: 0.4rem; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-muted); }
.files-input { background: var(--bg-inset); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.5rem 0.75rem; color: var(--text-primary); }
.files-row { background: var(--bg-inset); border: 1px solid var(--border); }
.files-path { color: var(--text-secondary); }
.files-reason { color: var(--danger-text); }
.files-btn { color: var(--text-primary); border: 1px solid var(--border); }
.files-cancel { color: var(--text-secondary); }
.files-cancel:hover { color: var(--text-primary); background: var(--bg-overlay); }
.files-primary { background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand); }
.files-primary:hover:not(:disabled) { filter: brightness(1.08); }
.files-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.files-input:focus-visible, .files-btn:focus-visible, .files-cancel:focus-visible, .files-primary:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--brand-glow); }
</style>
