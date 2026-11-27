<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { IconBook2, IconCheck, IconMinus, IconShieldCheck } from "@tabler/icons-vue";
import { api } from "../../services/api";
import { useSaveStatus } from "../../composables/useSaveStatus";
import { useOnboarding } from "../../composables/useOnboarding";
import { catalogStepCopy } from "../../utils/onboarding";
import SettingRow from "../settings/SettingRow.vue";
import StepShell from "./StepShell.vue";

const onboarding = useOnboarding();
const saveStatus = useSaveStatus();
const saving = ref(false);

const copy = computed(() =>
  catalogStepCopy(onboarding.catalogEnabled.value, onboarding.catalogInstalled.value),
);

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
    }
  } catch (error) {
    console.error("Failed to read catalog status", error);
  }
};

const onToggle = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const next = input.checked;

  saving.value = true;
  const ok = await saveStatus.run(async () => {
    const result = await api.saveSettings({ catalog_enabled: next });
    if (!result?.success) {
      throw new Error(result?.error || "Could not save the catalog setting.");
    }
  });
  if (ok) {
    onboarding.setCatalogState(next, next ? onboarding.catalogInstalled.value : false);
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
  <StepShell
    :icon="IconBook2"
    title="Game catalog"
    lead="Choose whether wLib downloads the shared game catalog to power Discover and fill in game details without extra requests."
  >
    <section class="step-card">
      <SettingRow
        label="Download the game catalog"
        control-id="onboarding-catalog-enabled"
        help-id="onboarding-catalog-help"
        :help="copy.help"
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
      </SettingRow>
    </section>

    <div class="explain-grid">
      <section
        class="explain-card"
        :class="{ 'explain-card--off': copy.tone === 'muted' }"
        data-testid="catalog-benefits"
        :data-catalog-tone="copy.tone"
      >
        <Transition name="catalog-list" mode="out-in">
          <div :key="copy.tone">
            <h3 class="explain-heading">
              <component
                :is="copy.tone === 'brand' ? IconCheck : IconMinus"
                class="w-4 h-4 shrink-0"
                :class="copy.tone === 'brand' ? 'explain-icon--brand' : 'explain-icon--muted'"
                aria-hidden="true"
              />
              <span>{{ copy.heading }}</span>
            </h3>
            <ul class="explain-list">
              <li v-for="item in copy.items" :key="item">
                <component
                  :is="copy.tone === 'brand' ? IconCheck : IconMinus"
                  class="w-3.5 h-3.5 shrink-0 mt-1"
                  :class="copy.tone === 'brand' ? 'explain-icon--brand' : 'explain-icon--muted'"
                  aria-hidden="true"
                />
                <span>{{ item }}</span>
              </li>
            </ul>
          </div>
        </Transition>
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
  </StepShell>
</template>

<style scoped>
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

/* Off: the same card, quieter. The text stays readable; only the tone drops. */
.explain-card--off .explain-heading {
  color: var(--text-secondary);
}

.explain-card--off .explain-list li {
  color: var(--text-muted);
}

.catalog-list-enter-active,
.catalog-list-leave-active {
  transition: opacity 140ms cubic-bezier(0.16, 1, 0.3, 1);
}

.catalog-list-enter-from,
.catalog-list-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .catalog-list-enter-active,
  .catalog-list-leave-active {
    transition: none;
  }
}
</style>
