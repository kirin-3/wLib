<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, type Component } from "vue";
import {
  IconBook2,
  IconCheck,
  IconFileImport,
  IconPuzzle,
  IconSettings,
  IconWorld,
} from "@tabler/icons-vue";
import { useOnboarding } from "../../composables/useOnboarding";
import { buildDoneSummary, type OnboardingIconKey } from "../../utils/onboarding";

const onboarding = useOnboarding();

const iconMap: Record<OnboardingIconKey, Component> = {
  sparkles: IconCheck,
  book: IconBook2,
  world: IconWorld,
  import: IconFileImport,
  puzzle: IconPuzzle,
  check: IconCheck,
};

const summaryRows = computed(() =>
  buildDoneSummary({
    catalogEnabled: onboarding.catalogEnabled.value,
    lcEnabled: onboarding.lcEnabled.value,
    importedCount: onboarding.importedCount.value,
    pairedBrowsers: onboarding.pairedBrowsers.value,
  }),
);

onMounted(() => {
  void onboarding.refreshPairing();
});
</script>

<template>
  <div class="done-step">
    <div class="done-head">
      <div class="done-badge" aria-hidden="true">
        <IconCheck class="w-8 h-8" :stroke-width="2.5" />
      </div>
      <h2 id="onboarding-step-title" tabindex="-1" class="done-title">
        You're all set
      </h2>
      <p class="done-lead">
        Here is a summary of your setup. You can change any of these choices at
        any time in Settings.
      </p>
    </div>

    <ul class="summary-list" aria-label="Setup summary">
      <li
        v-for="row in summaryRows"
        :key="row.id"
        class="summary-row"
        :data-summary-row="row.id"
      >
        <div class="summary-left">
          <span class="summary-icon" aria-hidden="true">
            <component :is="iconMap[row.icon]" class="w-4 h-4" />
          </span>
          <span class="summary-label">{{ row.label }}</span>
        </div>
        <span class="summary-pill" :class="`summary-pill--${row.tone}`">
          {{ row.value }}
        </span>
      </li>
    </ul>

    <p class="done-replay-hint">
      <IconSettings class="w-4 h-4 shrink-0" aria-hidden="true" />
      <span>
        Want to revisit this later? Open <strong>Settings → Advanced → Show setup again</strong>.
      </span>
    </p>
  </div>
</template>

<style scoped>
.done-step {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 36rem;
  margin: 0 auto;
  padding: 0.75rem 0;
}

.done-head {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.done-badge {
  width: 4rem;
  height: 4rem;
  border-radius: 9999px;
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1rem;
}

.done-title {
  font-size: 1.625rem;
  line-height: 1.25;
  font-weight: 750;
  color: var(--text-primary);
  text-wrap: balance;
  outline: none;
}

.done-lead {
  margin-top: 0.45rem;
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--text-secondary);
  text-wrap: pretty;
  max-width: 30rem;
}

.summary-list {
  list-style: none;
  margin: 0;
  padding: 0;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  overflow: hidden;
}

.summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.85rem 1rem;
  border-top: 1px solid var(--border);
}

.summary-row:first-child {
  border-top: 0;
}

.summary-left {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  min-width: 0;
}

.summary-icon {
  width: 1.875rem;
  height: 1.875rem;
  border-radius: 0.5rem;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.summary-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-primary);
}

.summary-pill {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.65rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 650;
  border: 1px solid transparent;
}

.summary-pill--brand {
  background: var(--brand-glow);
  color: var(--brand-text);
  border-color: color-mix(in srgb, var(--brand) 30%, transparent);
}

.summary-pill--success {
  background: var(--success-bg);
  color: var(--success-text);
  border-color: var(--success-border);
}

.summary-pill--muted {
  background: var(--bg-surface);
  color: var(--text-secondary);
  border-color: var(--border);
}

.done-replay-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-secondary);
  text-align: center;
}

.done-replay-hint strong {
  color: var(--text-primary);
  font-weight: 600;
}
</style>
