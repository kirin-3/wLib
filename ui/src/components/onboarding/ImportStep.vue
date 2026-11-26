<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconFileImport, IconInfoCircle } from "@tabler/icons-vue";
import { gamesStore } from "../../composables/useGamesStore";
import { useOnboarding } from "../../composables/useOnboarding";
import ExternalImportPanel from "../ExternalImportPanel.vue";

const onboarding = useOnboarding();
const existingCount = computed(() => gamesStore.games.value.length);
</script>

<template>
  <div class="step-shell">
    <header class="step-header">
      <div class="step-icon-tile" aria-hidden="true">
        <IconFileImport class="w-6 h-6" />
      </div>
      <div class="min-w-0">
        <h2 id="onboarding-step-title" tabindex="-1" class="step-title">
          Import from other apps
        </h2>
        <p class="step-lead">
          Bring your existing library from F95Checker, Atlas, XLibrary, f69,
          Playnite, or Game-List. You can skip this step and import later.
        </p>
      </div>
    </header>

    <div
      v-if="existingCount > 0"
      class="existing-note"
      role="status"
      data-testid="import-existing-games-note"
    >
      <IconInfoCircle class="w-4 h-4 shrink-0 mt-0.5 note-icon" aria-hidden="true" />
      <span>
        Your library already has {{ existingCount }}
        {{ existingCount === 1 ? "game" : "games" }}. Games matched in an import
        follow the merge mode you choose below.
      </span>
    </div>

    <div class="import-card">
      <ExternalImportPanel
        variant="embedded"
        @busy="onboarding.setBusy($event)"
        @imported="onboarding.recordImport($event)"
      />
    </div>
  </div>
</template>

<style scoped>
.step-shell {
  display: flex;
  flex-direction: column;
  gap: 1rem;
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

.existing-note {
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  padding: 0.75rem 0.9rem;
  border-radius: 0.625rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-primary);
}

.note-icon {
  color: var(--brand-text);
}

.import-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  overflow: hidden;
}
</style>
