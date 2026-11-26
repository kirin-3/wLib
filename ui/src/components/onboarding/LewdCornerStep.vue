<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref } from "vue";
import { IconCheck, IconInfoCircle, IconWorld } from "@tabler/icons-vue";
import { api } from "../../services/api";
import { useSaveStatus } from "../../composables/useSaveStatus";
import { useOnboarding } from "../../composables/useOnboarding";
import SettingRow from "../settings/SettingRow.vue";

const onboarding = useOnboarding();
const saveStatus = useSaveStatus();
const saving = ref(false);

const highlights = [
  "Shows LewdCorner thread fields when adding or editing games",
  "Fills in matching LewdCorner threads for games that are on both F95Zone and LewdCorner",
  "Checks LewdCorner alongside F95Zone for new versions and downloads",
];

const onToggle = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const next = input.checked;
  saving.value = true;
  const ok = await saveStatus.run(async () => {
    const result = await api.saveSettings({ lc_enabled: next });
    if (!result?.success) {
      throw new Error(result?.error || "Could not save the LewdCorner setting.");
    }
  });
  if (ok) {
    onboarding.setLcEnabled(next);
  } else {
    input.checked = !next;
  }
  saving.value = false;
};
</script>

<template>
  <div class="step-shell">
    <header class="step-header">
      <div class="step-icon-tile" aria-hidden="true">
        <IconWorld class="w-6 h-6" />
      </div>
      <div class="min-w-0">
        <h2 id="onboarding-step-title" tabindex="-1" class="step-title">
          LewdCorner
        </h2>
        <p class="step-lead">
          Turn this on if you also follow or download games from LewdCorner.
        </p>
      </div>
    </header>

    <section class="step-card">
      <SettingRow
        label="Use LewdCorner"
        control-id="onboarding-lc-enabled"
        help-id="onboarding-lc-help"
        help="Show LewdCorner thread fields and fill in LewdCorner threads for your games from the catalog. You can configure LC Plus later in Settings → Sources."
        layout="inline"
        :save-state="saveStatus.state.value"
        :save-error="saveStatus.error.value"
      >
        <label class="relative inline-flex items-center cursor-pointer shrink-0">
          <input
            id="onboarding-lc-enabled"
            type="checkbox"
            role="switch"
            :checked="onboarding.lcEnabled.value"
            :disabled="saving"
            class="sr-only peer"
            aria-describedby="onboarding-lc-help"
            data-controller-primary
            @change="onToggle"
          />
          <div class="ui-toggle"></div>
        </label>
      </SettingRow>
    </section>

    <div
      v-if="!onboarding.catalogEnabled.value"
      class="catalog-off-note"
      role="status"
      data-testid="lc-catalog-off-note"
    >
      <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5 note-icon" aria-hidden="true" />
      <p>
        The game catalog is turned off, so wLib cannot fill in LewdCorner threads
        from the catalog. You can still add LewdCorner threads by hand.
      </p>
    </div>

    <section class="explain-card">
      <h3 class="explain-heading">What Use LewdCorner adds</h3>
      <ul class="explain-list">
        <li v-for="item in highlights" :key="item">
          <IconCheck class="w-3.5 h-3.5 shrink-0 mt-1 check-icon" aria-hidden="true" />
          <span>{{ item }}</span>
        </li>
      </ul>
    </section>
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

.catalog-off-note {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.875rem 1rem;
  border-radius: 0.75rem;
  background: var(--bg-raised);
  border: 1px solid var(--border-hover);
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--text-primary);
}

.note-icon {
  color: var(--brand-text);
}

.explain-card {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem 1.125rem;
}

.explain-heading {
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

.check-icon {
  color: var(--brand-text);
}
</style>
