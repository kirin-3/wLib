<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { IconLoader2, IconX } from "@tabler/icons-vue";
import { api, type ScannedGame } from "../../services/api";
import { notify, notifyError } from "../../utils/toast";
import { useModalKeyboard } from "../../utils/modalKeyboard";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

const folder = ref("");
const games = ref<ScannedGame[]>([]);
const selected = ref(new Set<string>());
const scanned = ref(false);
const scanning = ref(false);
const importing = ref(false);

const newGames = computed(() => games.value.filter((game) => !game.in_library));
const selectedGames = computed(() =>
  games.value.filter((game) => selected.value.has(game.exe_path)),
);

const close = () => {
  if (importing.value) return;
  folder.value = "";
  games.value = [];
  selected.value = new Set();
  scanned.value = false;
  emit("update:modelValue", false);
};

const modalRef = ref<HTMLElement | null>(null);
useModalKeyboard(modalRef, () => props.modelValue, close);

const scan = async () => {
  const path = folder.value.trim();
  if (!path || scanning.value) return;
  scanning.value = true;
  try {
    const result = await api.scanGamesFolder(path);
    if (!result?.success) {
      notifyError(`Could not scan folder: ${result?.error || "Unknown error"}`);
      return;
    }
    games.value = result.games || [];
    selected.value = new Set(newGames.value.map((game) => game.exe_path));
    scanned.value = true;
  } catch (e) {
    notifyError(`Could not scan folder: ${String(e)}`);
  } finally {
    scanning.value = false;
  }
};

const browse = async () => {
  const path = await api.browseDirectory(folder.value);
  if (path) {
    folder.value = path;
    void scan();
  }
};

const toggle = (game: ScannedGame) => {
  const next = new Set(selected.value);
  if (!next.delete(game.exe_path)) next.add(game.exe_path);
  selected.value = next;
};

const toggleAll = () => {
  selected.value =
    selectedGames.value.length === newGames.value.length
      ? new Set()
      : new Set(newGames.value.map((game) => game.exe_path));
};

const importSelected = async () => {
  if (!selectedGames.value.length || importing.value) return;
  importing.value = true;
  try {
    const { added, errors } = await api.importGames(selectedGames.value);
    if (errors.length) notifyError(`Some games could not be added:\n\n${errors.join("\n")}`);
    if (added) notify(`Added ${added} game${added === 1 ? "" : "s"} to your library.`, "success");
  } finally {
    importing.value = false;
  }
  close();
};
</script>

<template>
  <div v-if="modelValue" class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>

    <div
      ref="modalRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-folder-title"
      tabindex="-1"
      class="modal-content w-full max-w-2xl rounded-2xl relative overflow-hidden flex flex-col max-h-[90vh] outline-none"
    >
      <div
        class="px-6 py-5 flex items-center justify-between"
        style="border-bottom: 1px solid var(--border)"
      >
        <div>
          <h3 id="import-folder-title" class="text-xl font-bold" style="color: var(--text-primary)">
            Import Folder
          </h3>
          <p class="text-xs mt-1" style="color: var(--text-muted)">
            Each subfolder with a game executable becomes one game. Titles, versions and engines are
            guessed from folder names and files, so check them after importing.
          </p>
        </div>
        <button
          @click="close"
          aria-label="Close"
          title="Close (Esc)"
          class="close-btn shrink-0 ml-4 p-1 rounded-lg"
        >
          <IconX class="w-5 h-5" />
        </button>
      </div>

      <div class="p-6 space-y-4 overflow-y-auto flex-1 min-h-0">
        <div class="flex gap-2">
          <input
            v-model="folder"
            type="text"
            placeholder="/path/to/games"
            aria-label="Games folder"
            class="modal-input flex-1 text-sm font-mono"
            @keydown.enter="scan"
          />
          <button @click="browse" class="modal-btn">Browse</button>
          <button @click="scan" :disabled="!folder.trim() || scanning" class="modal-btn">
            <IconLoader2 v-if="scanning" class="w-4 h-4 animate-spin" />
            <span v-else>Scan</span>
          </button>
        </div>

        <p v-if="scanned && !games.length" class="text-sm" style="color: var(--text-muted)">
          No games found in this folder's subfolders.
        </p>

        <div v-if="games.length" class="space-y-2">
          <div class="flex items-center justify-between text-xs" style="color: var(--text-muted)">
            <span>
              Found {{ games.length }}
              <template v-if="games.length !== newGames.length">
                · {{ games.length - newGames.length }} already in library
              </template>
            </span>
            <button v-if="newGames.length" @click="toggleAll" class="link-btn">
              {{ selectedGames.length === newGames.length ? "Select none" : "Select all" }}
            </button>
          </div>
          <ul class="scan-list rounded-lg overflow-hidden">
            <li v-for="game in games" :key="game.exe_path">
              <label
                class="scan-row flex items-center gap-3 px-3 py-2"
                :class="{ 'opacity-50': game.in_library }"
              >
                <input
                  type="checkbox"
                  :checked="selected.has(game.exe_path)"
                  :disabled="game.in_library"
                  @change="toggle(game)"
                />
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-medium truncate" style="color: var(--text-primary)">
                    {{ game.title }}
                    <span v-if="game.version" class="font-mono text-xs" style="color: var(--text-muted)">
                      v{{ game.version }}
                    </span>
                  </span>
                  <span class="block text-xs font-mono truncate" style="color: var(--text-muted)" :title="game.exe_path">
                    {{ game.exe_path }}
                  </span>
                </span>
                <span v-if="game.in_library" class="text-xs shrink-0" style="color: var(--text-muted)">In library</span>
                <span v-else-if="game.engine" class="engine-tag text-xs shrink-0">{{ game.engine }}</span>
              </label>
            </li>
          </ul>
        </div>
      </div>

      <div
        class="px-6 py-4 flex justify-end gap-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <button
          @click="close"
          :disabled="importing"
          class="px-5 py-2.5 rounded-lg text-sm font-medium"
          style="color: var(--text-secondary)"
        >
          Cancel
        </button>
        <button
          @click="importSelected"
          :disabled="!selectedGames.length || importing"
          class="text-white px-6 py-2.5 rounded-lg text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed"
          style="background: var(--brand); box-shadow: var(--shadow-brand)"
        >
          {{
            importing
              ? "Adding…"
              : `Add ${selectedGames.length} game${selectedGames.length === 1 ? "" : "s"}`
          }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}

.modal-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  color: var(--text-primary);
}
.modal-input::placeholder {
  color: var(--text-muted);
}
.modal-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.modal-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 4.5rem;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
}
.modal-btn:hover:not(:disabled) {
  background: var(--border-hover);
}
.modal-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.close-btn {
  color: var(--text-muted);
}
.close-btn:hover {
  color: var(--text-primary);
}

.link-btn {
  color: var(--brand);
}
.link-btn:hover {
  text-decoration: underline;
}

.scan-list {
  border: 1px solid var(--border);
}
.scan-list li + li {
  border-top: 1px solid var(--border);
}
.scan-row {
  background: var(--bg-raised);
  cursor: pointer;
}
.scan-row:hover {
  background: var(--bg-overlay);
}

.engine-tag {
  padding: 0.1rem 0.45rem;
  border-radius: 0.375rem;
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
</style>
