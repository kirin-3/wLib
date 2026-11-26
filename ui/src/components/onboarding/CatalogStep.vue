<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { IconBook2, IconCheck, IconInfoCircle, IconShieldCheck } from "@tabler/icons-vue";
import { api } from "../../services/api";
import { useSaveStatus } from "../../composables/useSaveStatus";
import { useOnboarding } from "../../composables/useOnboarding";
import SettingRow from "../settings/SettingRow.vue";

const onboarding = useOnboarding();
const saveStatus = useSaveStatus();
const saving = ref(false);
const hadInstalledCopy = ref(onboarding.catalogInstalled.value);
const showDeletedCopyNote = ref(false);

const whatYouGet = [
  "Browse and search games in Discover",
  "Automatic game details, covers, tags, and F95 ratings",
  "Matching LewdCorner threads when LewdCorner is enabled",
  "Linked DLsite, VNDB, Steam, and Ryuugames pages",
];

const whatWlibSends = [
  "Only plain requests for the shared catalog files",
  "No library list, game titles, or play history",
  "No F95Zone or LewdCorner account data",
  "No user identifier or tracking cookies",
];

const refreshStatus = async () => {
  try {
    const status = await api.getCatalogStatus();
    if (status?.success !== false) {
      const enabled = status.enabled !== false;
      const installed = status.installed === true;
      onboarding.setCatalogState(enabled, installed);
      if (installed) hadInstalledCopy.value = true;
    }
  } catch (error) {
    console.error("Failed to read catalog status", error);
  }
};

const onToggle = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const next = input.checked;
  const deletingCopy = !next && (hadInstalledCopy.value || onboarding.catalogInstalled.value);

  saving.value = true;
  const ok = await saveStatus.run(async () => {
    const result = await api.saveSettings({ catalog_enabled: next });
    if (!result?.success) {
      throw new Error(result?.error || "Could not save the catalog setting.");
    }
  });
  if (ok) {
    onboarding.setCatalogState(next, next ? onboarding.catalogInstalled.value : false);
    showDeletedCopyNote.value = deletingCopy;
  } else {
    input.checked = !next;
  }
  saving.value = false;
};

onMounted(() => {
  void refreshStatus();
});
</script>

<template>
  <div class="step-shell">
    <header class="step-header">
      <div class="step-icon-tile" aria-hidden="true">
        <IconBook2 class="w-6 h-6" />
      </div>
      <div class="min-w-0">
        <h2 id="onboarding-step-title" tabindex="-1" class="step-title">
          Game catalog
        </h2>
        <p class="step-lead">
          Choose whether wLib downloads the shared game catalog to power Discover
          and fill in game details without extra requests.
        </p>
      </div>
    </header>

    <section class="step-card">
      <SettingRow
        label="Download the game catalog"
        control-id="onboarding-catalog-enabled"
        help-id="onboarding-catalog-help"
        help="Checked in the background at most twice a day. You can change this anytime in Settings → Sources."
        layout="inline"
        :save-state="saveStatus.state.value"
        :save-error="saveStatus.error.value"
      >
        <label class="relative inline-flex items-center cursor-pointer shrink-0">
          <input
            id="onboarding-catalog-enabled"
            type="checkbox"
            role="switch"
            :checked="onboarding.catalogEnabled.value"
            :disabled="saving"
            class="sr-only peer"
            aria-describedby="onboarding-catalog-help"
            data-controller-primary
            @change="onToggle"
          />
          <div class="ui-toggle"></div>
        </label>
        <template v-if="showDeletedCopyNote" #after>
          <p class="step-note" role="status" data-testid="catalog-deleted-note">
            <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <span>wLib deletes its local catalog copy when the catalog is turned off.</span>
          </p>
        </template>
      </SettingRow>
    </section>

    <div class="explain-grid">
      <section class="explain-card">
        <h3 class="explain-heading">
          <IconCheck class="w-4 h-4 shrink-0 explain-icon--brand" aria-hidden="true" />
          <span>What you get</span>
        </h3>
        <ul class="explain-list">
          <li v-for="item in whatYouGet" :key="item">
            <IconCheck class="w-3.5 h-3.5 shrink-0 mt-1 explain-icon--brand" aria-hidden="true" />
            <span>{{ item }}</span>
          </li>
        </ul>
      </section>

      <section class="explain-card">
        <h3 class="explain-heading">
          <IconShieldCheck class="w-4 h-4 shrink-0 explain-icon--muted" aria-hidden="true" />
          <span>What wLib sends</span>
        </h3>
        <ul class="explain-list">
          <li v-for="item in whatWlibSends" :key="item">
            <IconShieldCheck class="w-3.5 h-3.5 shrink-0 mt-1 explain-icon--muted" aria-hidden="true" />
            <span>{{ item }}</span>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.step-shell {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.step-header {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.step-icon-tile {
  width: 3rem;
  height: 3rem;
  border-radius: 0.75rem;
  background: var(--brand-glow);
  color: var(--brand-text);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.step-title {
  font-size: 1.5rem;
  line-height: 1.25;
  font-weight: 700;
  color: var(--text-primary);
  text-wrap: balance;
  outline: none;
}

.step-lead {
  margin-top: 0.35rem;
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--text-secondary);
  text-wrap: pretty;
  max-width: 38rem;
}

.step-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem 1.125rem;
}

.step-card :deep(.setting-row.ui-card-row) {
  border-top: 0;
  padding-top: 0;
  margin-top: 0;
}

.step-note {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-secondary);
}

.explain-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.875rem;
}

@media (max-width: 40rem) {
  .explain-grid {
    grid-template-columns: 1fr;
  }
}

.explain-card {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem 1.125rem;
}

.explain-heading {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.875rem;
  font-weight: 650;
  color: var(--text-primary);
  margin-bottom: 0.75rem;
}

.explain-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.explain-list li {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-secondary);
}

.explain-icon--brand {
  color: var(--brand-text);
}

.explain-icon--muted {
  color: var(--text-secondary);
}
</style>
