<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import { IconFileImport, IconInfoCircle } from "@tabler/icons-vue";
import { gamesStore } from "../../composables/useGamesStore";
import { useOnboarding, type PendingImport } from "../../composables/useOnboarding";
import ExternalImportPanel from "../ExternalImportPanel.vue";
import StepShell from "./StepShell.vue";

const onboarding = useOnboarding();
const existingCount = computed(() => gamesStore.games.value.length);

// The overlay footer runs the panel's import; the panel hides its own button.
let registered: PendingImport | null = null;
const onPendingImport = (request: PendingImport | null) => {
  registered = request;
  onboarding.setPendingImport(request);
};
onBeforeUnmount(() => {
  // Clear only our own request, never one a newer step instance set.
  if (registered && onboarding.pendingImport.value === registered) {
    onboarding.setPendingImport(null);
  }
  registered = null;
});
</script>

<template>
  <StepShell
    :icon="IconFileImport"
    title="Import from other apps"
    lead="Bring your existing library from F95Checker, Atlas, XLibrary, f69, Playnite, or Game-List. You can skip this step and import later."
  >
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
        @pending-import="onPendingImport"
      />
    </div>
  </StepShell>
</template>

<style scoped>
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
