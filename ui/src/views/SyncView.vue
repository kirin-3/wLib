<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { IconCloud } from "@tabler/icons-vue";
import LibrarySyncPanel from "../components/LibrarySyncPanel.vue";
</script>

<template>
  <div class="allow-text-selection sync-page p-8 max-w-5xl pb-12">
    <header class="mb-8">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconCloud class="ui-page-heading-icon" />
        <span>Sync</span>
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Keep your library in step across machines.
      </p>
    </header>

    <LibrarySyncPanel />
  </div>
</template>

<style scoped>
.sync-page { color: var(--text-primary); min-width: 0; width: 100%; }
@media (max-width: 640px) { .sync-page { padding: 1rem; } }
</style>
