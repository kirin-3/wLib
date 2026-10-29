<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconDatabase } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { CatalogStatusResponse } from '../services/api';
import { catalogSourceLines, catalogSummary, formatCatalogTime } from '../utils/catalog';
import { notifyError } from '../utils/toast';

const status = ref<CatalogStatusResponse | null>(null);
const saving = ref(false);
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
  if (!next && !confirm('Turn off the game catalog? wLib stops downloading it and deletes the local copy.')) {
    input.checked = true;
    return;
  }
  saving.value = true;
  try {
    const result = await api.saveSettings({ catalog_enabled: next });
    if (!result.success) throw new Error(result.error || 'Could not save the catalog setting.');
  } catch (e) {
    input.checked = !next;
    notifyError(e instanceof Error ? e.message : String(e));
  } finally {
    saving.value = false;
    await refresh();
  }
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
  <section id="catalog" class="catalog-card rounded-xl p-6 space-y-5" aria-labelledby="catalog-heading">
    <h3 id="catalog-heading" class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
      <IconDatabase class="ui-section-icon" />
      Game catalog
    </h3>

    <div class="catalog-row flex items-center justify-between gap-4 p-4 rounded-lg">
      <div>
        <h4 class="text-sm font-medium" style="color: var(--text-primary)">Download the game catalog</h4>
        <p class="text-xs mt-1" style="color: var(--text-muted)">
          A shared list of games from F95Zone, DLsite, Ryuugames and VNDB, checked at most twice a day.
          wLib only downloads it; nothing from your library is sent.
        </p>
      </div>
      <label class="relative inline-flex items-center cursor-pointer shrink-0">
        <input
          type="checkbox"
          :checked="enabled"
          :disabled="saving || !status"
          class="sr-only peer"
          aria-label="Download the game catalog"
          @change="toggle"
        />
        <div class="ui-toggle"></div>
      </label>
    </div>

    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="space-y-1 text-sm" role="status">
        <p style="color: var(--text-primary)">{{ summary }}</p>
        <p v-if="enabled && status?.success !== false" class="text-xs" style="color: var(--text-muted)">
          Last checked: {{ formatCatalogTime(status?.last_check) }}<span v-if="status?.checking"> · checking now…</span>
        </p>
      </div>
      <button class="catalog-btn" :disabled="!enabled || status?.checking || !status" @click="checkNow">
        {{ status?.checking ? 'Checking…' : 'Check now' }}
      </button>
    </div>

    <p v-if="enabled && status?.last_error" class="text-xs" role="alert" style="color: var(--danger-text)">
      Last check failed: {{ status.last_error }}
    </p>

    <table v-if="enabled && sources.length" class="catalog-table w-full text-xs">
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

    <p class="text-xs" style="color: var(--text-muted)">
      VNDB data comes from
      <a href="https://vndb.org/" @click.prevent="api.openInBrowser('https://vndb.org/')">The Visual Novel Database</a>
      and is available under the
      <a
        href="https://opendatacommons.org/licenses/odbl/1-0/"
        @click.prevent="api.openInBrowser('https://opendatacommons.org/licenses/odbl/1-0/')"
      >Open Database License</a>.
    </p>
  </section>
</template>

<style scoped>
.catalog-card { color: var(--text-primary); background: var(--bg-surface); border: 1px solid var(--border); scroll-margin-top: 1.5rem; }
.catalog-row { background: var(--bg-raised); border: 1px solid var(--border); }
.catalog-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
  transition: background-color 0.15s ease;
}
.catalog-btn:hover:not(:disabled) { background: var(--border-hover); }
.catalog-btn:disabled { opacity: 0.5; cursor: default; }
.catalog-table { border-collapse: collapse; color: var(--text-secondary); }
.catalog-table th { color: var(--text-muted); font-weight: 600; text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); }
.catalog-table th.text-right { text-align: right; }
.catalog-table td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); }
.catalog-table tr:last-child td { border-bottom: none; }
a { color: var(--brand); text-decoration: underline; }
</style>
