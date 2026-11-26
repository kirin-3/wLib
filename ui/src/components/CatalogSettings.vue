<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { confirmAction } from "../utils/confirm";
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { api, onWebviewReady } from '../services/api';
import type { CatalogStatusResponse } from '../services/api';
import { catalogSourceLines, catalogSummary, formatCatalogTime } from '../utils/catalog';
import { notifyError } from '../utils/toast';
import { useSaveStatus } from '../composables/useSaveStatus';
import SettingRow from './settings/SettingRow.vue';
import SettingsGroup from './settings/SettingsGroup.vue';

const settingsRowVisible = useSettingsRowVisibility();

const status = ref<CatalogStatusResponse | null>(null);
const saving = ref(false);
const saveStatus = useSaveStatus();
const enabled = computed(() => status.value?.enabled !== false);
const summary = computed(() => catalogSummary(status.value));
const sources = computed(() => catalogSourceLines(status.value));
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

const refresh = async () => {
  if (timer) clearTimeout(timer);
  try {
    const next = await api.getCatalogStatus();
    if (disposed) return;
    status.value = next;
    // A check runs in the background; follow it until it finishes.
    if (next.checking) timer = setTimeout(() => { void refresh(); }, 1500);
  } catch (e) {
    if (!disposed) status.value = { success: false, error: String(e) };
  }
};

const toggle = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const next = input.checked;
  if (!next && !(await confirmAction({ title: 'Turn off catalog', message: 'Turn off the game catalog? wLib stops downloading it and deletes the local copy.', confirmLabel: 'Turn off', danger: true }))) {
    input.checked = true;
    return;
  }
  saving.value = true;
  const saved = await saveStatus.run(async () => {
    const result = await api.saveSettings({ catalog_enabled: next });
    if (!result.success) throw new Error(result.error || 'Could not save the catalog setting.');
  });
  if (!saved) input.checked = !next;
  saving.value = false;
  await refresh();
};

const checkNow = async () => {
  try {
    const result = await api.checkCatalogNow();
    if (!result.success) throw new Error(result.error || 'Could not check the catalog.');
    status.value = { ...result, checking: true };
  } catch (e) {
    notifyError(e instanceof Error ? e.message : String(e));
  }
  await refresh();
};

onMounted(() => onWebviewReady(() => { void refresh(); }));
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); });
</script>

<template>
  <SettingsGroup id="catalog" title="Game catalog">

    <SettingRow data-settings-row="catalog-static-0" data-settings-bound v-show="settingsRowVisible('catalog-static-0')"
      label="Download the game catalog" control-id="catalog-enabled"
      help="A shared list of games from F95Zone, DLsite, Ryuugames and VNDB, checked at most twice a day. wLib only downloads it; nothing from your library is sent."
      :save-state="saveStatus.state.value" :save-error="saveStatus.error.value">
      <label class="relative inline-flex items-center cursor-pointer shrink-0">
        <input
          id="catalog-enabled"
          type="checkbox"
          role="switch"
          :checked="enabled"
          :disabled="saving || !status"
          class="sr-only peer"
          aria-label="Download the game catalog"
          @change="toggle"
        />
        <div class="ui-toggle"></div>
      </label>
    </SettingRow>

    <div data-settings-row="catalog-static-1" data-settings-bound v-show="settingsRowVisible('catalog-static-1')" class="ui-card-row flex flex-wrap items-start justify-between gap-3">
      <div class="space-y-1 text-sm" role="status">
        <p style="color: var(--text-primary)">{{ summary }}</p>
        <p v-if="enabled && status?.success !== false" class="text-xs" style="color: var(--text-muted)">
          Last checked: {{ formatCatalogTime(status?.last_check) }}<span v-if="status?.checking"> · checking now…</span>
        </p>
      </div>
      <button class="ui-card-btn" :disabled="!enabled || status?.checking || !status" @click="checkNow">
        {{ status?.checking ? 'Checking…' : 'Check now' }}
      </button>
    </div>

    <p data-settings-row="catalog-static-2" data-settings-bound v-show="settingsRowVisible('catalog-static-2')" v-if="enabled && status?.last_error" class="settings-row-attached text-xs" role="alert" style="color: var(--danger-text)">
      Last check failed: {{ status.last_error }}
    </p>

    <table data-settings-row="catalog-static-3" data-settings-bound v-show="settingsRowVisible('catalog-static-3')" v-if="enabled && sources.length" class="catalog-table w-full text-xs">
      <thead>
        <tr>
          <th scope="col">Source</th>
          <th scope="col" class="text-right">Entries</th>
          <th scope="col" class="text-right">Updated</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="line in sources" :key="line.key">
          <td>{{ line.label }}</td>
          <td class="text-right tabular-nums">{{ line.count }}</td>
          <td class="text-right">{{ line.updated }}</td>
        </tr>
      </tbody>
    </table>

    <p data-settings-row="catalog-static-4" data-settings-bound v-show="settingsRowVisible('catalog-static-4')" class="text-xs" style="color: var(--text-muted)">
      VNDB data comes from
      <a href="https://vndb.org/" @click.prevent="api.openInBrowser('https://vndb.org/')">The Visual Novel Database</a>
      and is available under the
      <a
        href="https://opendatacommons.org/licenses/odbl/1-0/"
        @click.prevent="api.openInBrowser('https://opendatacommons.org/licenses/odbl/1-0/')"
      >Open Database License</a>.
    </p>
  </SettingsGroup>
</template>

<style scoped>
.catalog-table { border-collapse: collapse; color: var(--text-secondary); }
.catalog-table th { color: var(--text-muted); font-weight: 600; text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); }
.catalog-table th.text-right { text-align: right; }
.catalog-table td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); }
.catalog-table tr:last-child td { border-bottom: none; }
a { color: var(--brand); text-decoration: underline; }
</style>
