<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { IconAlertTriangle } from "@tabler/icons-vue";
import { api, type GameFilePlanEntry, type GameRecord, type UninstallOptions } from "../services/api";
import { actionableEntries, backupDefault, confirmReason, planProblem, showBackupOption } from "../utils/gameFiles";
import { useModalKeyboard } from "../utils/modalKeyboard";

const props = defineProps<{ modelValue: boolean; games: GameRecord[] }>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "confirm", games: GameRecord[], options: UninstallOptions): void;
}>();

const dialog = ref<HTMLElement | null>(null);
const entries = ref<GameFilePlanEntry[]>([]);
const overrides = ref<Record<string, string>>({});
const loading = ref(false);
const loadError = ref("");
const backup = ref(false);
const removeFromLibrary = ref(false);
let request = 0;

const close = () => emit("update:modelValue", false);
useModalKeyboard(dialog, () => props.modelValue, close);

const backupVisible = computed(() => showBackupOption(entries.value));
const reason = computed(() => loadError.value || confirmReason(entries.value, loading.value, "uninstall"));
const goingCount = computed(() => actionableEntries(entries.value).length);
const single = computed(() => entries.value.length === 1);

watch(() => props.modelValue, async (open) => {
  if (!open) return;
  const current = ++request;
  entries.value = [];
  overrides.value = {};
  removeFromLibrary.value = false;
  loadError.value = "";
  loading.value = true;
  await nextTick();
  dialog.value?.focus();
  try {
    const res = await api.planGameUninstall(props.games.map((game) => game.id));
    if (current !== request) return;
    if (!res.success) loadError.value = res.error || "Could not check the game folders.";
    entries.value = res.games ?? [];
    backup.value = backupDefault(entries.value);
  } catch (error) {
    if (current === request) loadError.value = String(error);
  } finally {
    if (current === request) loading.value = false;
  }
});

const changeRoot = async (entry: GameFilePlanEntry) => {
  let picked = "";
  try { picked = await api.browseDirectory(entry.root); }
  catch (error) { console.error("Failed to browse directory", error); }
  if (!picked) return;
  const key = String(entry.game_id);
  overrides.value = { ...overrides.value, [key]: picked };
  const res = await api.planGameUninstall([entry.game_id], { [key]: picked });
  const next = res.games?.[0];
  if (next) entries.value = entries.value.map((item) => (item.game_id === entry.game_id ? next : item));
};

const confirm = () => {
  if (reason.value) return;
  const going = new Set(actionableEntries(entries.value).map((entry) => entry.game_id));
  const rootOverrides = Object.fromEntries(Object.entries(overrides.value).filter(([id]) => going.has(Number(id))));
  emit("confirm", props.games.filter((game) => going.has(game.id)), {
    backup_saves: backupVisible.value && backup.value,
    remove_from_library: removeFromLibrary.value,
    root_overrides: rootOverrides,
  });
  close();
};
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="uninstall-title" tabindex="-1" class="files-dialog w-full max-w-xl rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none">
        <div class="px-6 pt-6 pb-4 overflow-y-auto min-h-0 space-y-4">
          <div class="flex gap-4">
            <span class="files-icon" aria-hidden="true"><IconAlertTriangle class="w-5 h-5" /></span>
            <div class="min-w-0 pt-1">
              <h3 id="uninstall-title" class="text-lg font-bold" style="color: var(--text-primary)">{{ single ? `Uninstall ${entries[0]?.title || games[0]?.title || ""}?` : `Uninstall ${games.length} games?` }}</h3>
              <p class="mt-1 text-sm" style="color: var(--text-secondary)">wLib deletes the folder shown for each game. You can undo for a few seconds after you confirm.</p>
            </div>
          </div>

          <p v-if="loading" class="text-sm" style="color: var(--text-muted)">Checking the game folders...</p>
          <ul v-else class="space-y-2" aria-label="Games to uninstall">
            <li v-for="entry in entries" :key="entry.game_id" class="files-row rounded-lg p-3">
              <div class="flex items-start gap-2">
                <div class="min-w-0 flex-1">
                  <div v-if="!single" class="text-sm font-semibold truncate" style="color: var(--text-primary)">{{ entry.title }}</div>
                  <div v-if="entry.root" class="files-path font-mono text-xs break-all">{{ entry.root }}</div>
                  <div v-if="entry.root && !planProblem(entry)" class="mt-1 text-xs" style="color: var(--text-muted)">
                    <template v-if="entry.saves_inside_root">Saves are inside this folder.</template>
                    <template v-else-if="entry.saves_folder">Saves stay in {{ entry.saves_folder }}</template>
                    <template v-else>No save folder found</template>
                  </div>
                  <div v-if="planProblem(entry)" class="files-reason mt-1 text-xs" role="note">{{ planProblem(entry) }}</div>
                </div>
                <button v-if="entry.root && !entry.blocked_reason" type="button" class="ui-action-btn files-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface shrink-0" :aria-label="`Change the folder of ${entry.title}`" @click="changeRoot(entry)">Change...</button>
              </div>
            </li>
          </ul>

          <div class="space-y-2">
            <label v-if="backupVisible" class="flex items-center gap-2 text-sm" style="color: var(--text-primary)">
              <input v-model="backup" type="checkbox" />
              Back up saves first
            </label>
            <label class="flex items-center gap-2 text-sm" style="color: var(--text-primary)">
              <input v-model="removeFromLibrary" type="checkbox" />
              Also remove from library
            </label>
            <p class="text-xs" style="color: var(--text-muted)">{{ removeFromLibrary ? "The game leaves the library. Save backups stay." : "The game stays in the library as Wishlist, with its playtime, notes and status." }}</p>
          </div>
        </div>
        <div class="px-6 py-4 flex items-center justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <span v-if="reason && !loading" class="mr-auto text-xs" style="color: var(--text-muted)">{{ reason }}</span>
          <span v-else-if="!single && goingCount < entries.length" class="mr-auto text-xs" style="color: var(--text-muted)">{{ entries.length - goingCount }} skipped</span>
          <button class="files-cancel px-5 py-2.5 rounded-lg text-sm font-medium" @click="close">Cancel</button>
          <button class="files-danger px-6 py-2.5 rounded-lg text-sm font-bold" :disabled="!!reason" :title="reason" @click="confirm">{{ single || goingCount === 1 ? "Uninstall" : `Uninstall ${goingCount}` }}</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.files-dialog { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.files-icon { display: inline-flex; align-items: center; justify-content: center; width: 2.5rem; height: 2.5rem; flex-shrink: 0; border-radius: 9999px; background: var(--danger-bg); color: var(--danger-text); border: 1px solid var(--danger-border); }
.files-row { background: var(--bg-inset); border: 1px solid var(--border); }
.files-path { color: var(--text-secondary); }
.files-reason { color: var(--danger-text); }
.files-btn { color: var(--text-primary); border: 1px solid var(--border); }
.files-cancel { color: var(--text-secondary); }
.files-cancel:hover { color: var(--text-primary); background: var(--bg-overlay); }
.files-danger { background: #dc2626; color: #fff; box-shadow: 0 4px 14px rgba(220, 38, 38, 0.3); }
.files-danger:hover:not(:disabled) { background: #b91c1c; }
.files-danger:disabled { opacity: 0.5; cursor: not-allowed; }
.files-btn:focus-visible, .files-cancel:focus-visible, .files-danger:focus-visible, input[type="checkbox"]:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--brand-glow); }
input[type="checkbox"] { accent-color: var(--brand); }
</style>
