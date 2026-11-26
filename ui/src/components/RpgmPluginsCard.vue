<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { IconLoader2 } from "@tabler/icons-vue";
import { api, type RpgmPluginEntry, type RpgmPluginsState } from "../services/api";
import { confirmAction } from "../utils/confirm";
import {
  filterPlugins,
  issueLabel,
  issueText,
  pluginCounts,
  pluginRestoreState,
  pluginSwitchState,
  restoreConfirmation,
  switchConfirmation,
} from "../utils/rpgmPlugins";
import { notify, notifyError } from "../utils/toast";

const props = defineProps<{
  gameId: number;
  // True while the Tools tab shows; the card loads the list again each time.
  active: boolean;
  // Another tool of this tab runs: no plugin change may start.
  locked: boolean;
}>();

const emit = defineEmits<{ busy: [busy: boolean] }>();

const state = ref<RpgmPluginsState | null>(null);
const loading = ref(false);
const loadError = ref("");
const writing = ref(false);
const writingIndex = ref<number | null>(null);
const search = ref("");
let requestId = 0;
let loadedFor: number | null = null;

const load = async (gameId: number, keepVisible = false): Promise<void> => {
  const request = ++requestId;
  loading.value = true;
  loadError.value = "";
  if (!keepVisible) state.value = null;
  try {
    const result = await api.getRpgmPlugins(gameId);
    if (request !== requestId || props.gameId !== gameId) return;
    if (result.success === false) {
      state.value = null;
      loadError.value = result.error || "Could not read the plugin list";
      return;
    }
    state.value = result;
    loadedFor = gameId;
  } catch (error) {
    if (request !== requestId || props.gameId !== gameId) return;
    state.value = null;
    loadError.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (request === requestId) loading.value = false;
  }
};

watch(
  () => [props.gameId, props.active] as const,
  ([gameId, active], previous) => {
    if (previous && previous[0] !== gameId) {
      // Another game: drop the old list and any late response for it.
      requestId++;
      state.value = null;
      loadError.value = "";
      loading.value = false;
      search.value = "";
      writing.value = false;
      writingIndex.value = null;
      loadedFor = null;
    }
    if (active) void load(gameId, loadedFor === gameId);
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  requestId++;
  if (writing.value) emit("busy", false);
});

const blocked = computed(() => writing.value || props.locked);
const entries = computed<RpgmPluginEntry[]>(() => state.value?.entries ?? []);
const shown = computed(() => filterPlugins(entries.value, search.value));
const counts = computed(() => pluginCounts(entries.value));
const restore = computed(() => (state.value ? pluginRestoreState(state.value, blocked.value) : null));
const engineLabel = computed(() => {
  const engine = state.value?.engine;
  return engine === "mz" ? "RPG Maker MZ" : engine === "mv" ? "RPG Maker MV" : "Engine not known";
});

const write = async (gameId: number, index: number | null, call: () => Promise<RpgmPluginsState>): Promise<void> => {
  writing.value = true;
  writingIndex.value = index;
  emit("busy", true);
  try {
    const result = await call();
    if (props.gameId !== gameId) return;
    if (result.success === false) {
      notifyError(result.error || "The plugin list was not changed");
      if (result.code === "stale") await load(gameId, true);
      return;
    }
    state.value = result;
  } catch (error) {
    if (props.gameId === gameId) notifyError(error instanceof Error ? error.message : String(error));
  } finally {
    if (props.gameId === gameId) {
      writing.value = false;
      writingIndex.value = null;
      emit("busy", false);
    }
  }
};

// The click is always prevented, so the switch shows the stored state until
// the write returns. The switch keeps focus: it is never removed or natively
// disabled during its own write.
const toggle = async (entry: RpgmPluginEntry): Promise<void> => {
  const current = state.value;
  if (!current || blocked.value || !pluginSwitchState(entry, false).enabled) return;
  const gameId = props.gameId;
  const next = !entry.enabled;
  const confirm = switchConfirmation(current, entry, next);
  if (confirm && !(await confirmAction(confirm))) return;
  if (props.gameId !== gameId || blocked.value) return;
  await write(gameId, entry.index, () =>
    api.setRpgmPluginEnabled(gameId, entry.index, next, current.revision ?? ""),
  );
};

const restoreList = async (): Promise<void> => {
  const current = state.value;
  if (!current || blocked.value || !restore.value?.enabled) return;
  const gameId = props.gameId;
  const confirm = restoreConfirmation(current);
  if (confirm && !(await confirmAction(confirm))) return;
  if (props.gameId !== gameId || blocked.value) return;
  await write(gameId, null, () => api.restoreRpgmPlugins(gameId, current.revision ?? "", confirm !== null));
  if (props.gameId === gameId && state.value !== current && state.value?.backup?.state === "same") {
    notify("The plugin list was restored from the backup.", "success");
  }
};

const switchLabel = (entry: RpgmPluginEntry): string =>
  `${entry.name} (entry ${entry.index + 1}), ${entry.enabled ? "on" : "off"}`;
</script>

<template>
  <section class="plugins-card space-y-3" aria-labelledby="tools-plugins-title" :aria-busy="loading || writing">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <span id="tools-plugins-title" class="text-sm font-medium" style="color: var(--text-primary)">Plugins</span>
      <span v-if="state?.readable && entries.length" class="text-xs" style="color: var(--text-secondary)">
        {{ counts.on }} on · {{ counts.off }} off · {{ engineLabel }}
      </span>
    </div>

    <p v-if="loading && !state" class="text-xs flex items-center gap-2" style="color: var(--text-secondary)" role="status">
      <IconLoader2 class="ui-action-icon animate-spin" />
      Reading the plugin list...
    </p>
    <div v-else-if="loadError" class="space-y-2">
      <p role="alert" class="text-xs" style="color: var(--danger-text)">{{ loadError }}</p>
      <button type="button" class="plugins-btn" @click="load(gameId)">Try again</button>
    </div>

    <template v-else-if="state">
      <p v-if="!state.readable" role="status" class="text-xs" style="color: var(--warning-text)">
        <strong>Read only:</strong> {{ state.reason || "The plugin list cannot be read." }}
        wLib changes only a plugin list that it can read without running code.
      </p>

      <div v-if="restore?.visible || restore?.reason" class="flex items-center gap-3 flex-wrap">
        <button
          v-if="restore?.visible"
          type="button"
          class="plugins-btn"
          :class="{ 'plugins-btn--danger': restore.confirm }"
          :aria-disabled="!restore.enabled"
          @click="restoreList"
        >
          Restore plugin list
        </button>
        <p v-if="restore?.visible" class="text-xs" style="color: var(--text-muted)">
          {{ restore.confirm ? "The list also has other changes, such as from a game update." : "Puts back the list from before the first change." }}
        </p>
        <p v-if="restore?.reason" class="text-xs" style="color: var(--warning-text)">{{ restore.reason }}</p>
      </div>

      <template v-if="state.readable">
        <p v-if="!entries.length" class="text-xs" style="color: var(--text-muted)">This game has no plugins.</p>
        <template v-else>
          <input
            v-model="search"
            type="search"
            class="plugins-search"
            placeholder="Search plugins"
            aria-label="Search plugins by name or description"
          />
          <p v-if="!shown.length" role="status" class="text-xs" style="color: var(--text-muted)">
            No plugin matches “{{ search.trim() }}”.
          </p>
          <ul v-else class="plugins-list" aria-label="Plugins in load order">
            <li
              v-for="entry in shown"
              :key="entry.index"
              class="plugins-row"
              :aria-busy="writingIndex === entry.index"
            >
              <label
                class="relative inline-flex items-center shrink-0"
                :class="pluginSwitchState(entry, blocked).enabled ? 'cursor-pointer' : 'cursor-not-allowed'"
                :title="pluginSwitchState(entry, false).reason || undefined"
              >
                <input
                  type="checkbox"
                  class="sr-only peer"
                  :checked="entry.enabled"
                  :aria-label="switchLabel(entry)"
                  :disabled="!pluginSwitchState(entry, false).enabled"
                  :aria-disabled="blocked ? 'true' : undefined"
                  @click.prevent="toggle(entry)"
                />
                <div class="ui-toggle" :class="{ 'plugins-toggle--blocked': blocked }"></div>
              </label>
              <div class="flex-1 min-w-0">
                <p class="text-sm flex items-center gap-2 min-w-0" style="color: var(--text-primary)">
                  <span class="plugins-index" aria-hidden="true">{{ entry.index + 1 }}</span>
                  <span class="truncate font-mono" :title="entry.name">{{ entry.name }}</span>
                  <IconLoader2 v-if="writingIndex === entry.index" class="ui-action-icon animate-spin shrink-0" aria-hidden="true" />
                </p>
                <p v-if="entry.description" class="text-xs truncate" style="color: var(--text-muted)" :title="entry.description">
                  {{ entry.description }}
                </p>
                <p v-if="!entry.enabled && !entry.can_enable" class="text-xs" style="color: var(--text-muted)">
                  Cannot be switched on: {{ pluginSwitchState(entry, false).reason.toLowerCase() }}.
                </p>
                <p v-if="entry.needed_by.length" class="text-xs" style="color: var(--text-secondary)">
                  Needed by {{ entry.needed_by.length }} loaded {{ entry.needed_by.length === 1 ? "plugin" : "plugins" }}
                </p>
                <ul v-if="entry.issues.length" class="space-y-0.5 mt-0.5">
                  <li
                    v-for="(issue, i) in entry.issues"
                    :key="i"
                    class="text-xs"
                    :style="{ color: issue.level === 'warning' ? 'var(--warning-text)' : 'var(--text-muted)' }"
                  >
                    <strong>{{ issueLabel(issue) }}:</strong> {{ issueText(issue) }}
                  </li>
                </ul>
              </div>
            </li>
          </ul>
        </template>
      </template>

      <p class="text-xs" style="color: var(--text-muted)">
        A backup, <code>js/plugins.js.wlib-backup</code>, is made before the first change. Checks cover missing files and
        declared dependencies (MZ only); a plugin can still need another without saying so. A game update replaces the
        list and does not keep plugin changes.
      </p>
    </template>
  </section>
</template>

<style scoped>
.plugins-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1rem;
}

.plugins-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.15s ease;
}

.plugins-btn:hover:not([aria-disabled="true"]) {
  background: var(--border-hover);
}

.plugins-btn[aria-disabled="true"] {
  cursor: not-allowed;
  opacity: 0.5;
}

.plugins-btn--danger {
  color: var(--danger-text);
}

.plugins-search {
  width: 100%;
  background: var(--bg-inset);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  border-radius: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
}

.plugins-list {
  max-height: 24rem;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

.plugins-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
}

.plugins-row + .plugins-row {
  border-top: 1px solid var(--border);
}

.plugins-index {
  min-width: 1.75rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.plugins-toggle--blocked {
  opacity: 0.55;
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  color: var(--text-secondary);
}
</style>
