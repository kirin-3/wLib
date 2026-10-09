<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { IconCloud } from "@tabler/icons-vue";
import LibrarySyncPanel from "../components/LibrarySyncPanel.vue";
</script>

<template>
  <div class="allow-text-selection sync-page p-8 max-w-5xl pb-12">
    <header class="sync-page-header">
      <IconCloud class="sync-title-icon" />
      <div>
        <h2 class="sync-page-title">Sync</h2>
        <p>Keep your library in step across machines.</p>
      </div>
    </header>

    <LibrarySyncPanel />
  </div>
</template>

<style scoped>
.sync-page { color: var(--text-primary); min-width: 0; width: 100%; }
.sync-page-header { display: flex; align-items: flex-start; gap: 0.75rem; margin-bottom: 1.5rem; }
.sync-title-icon { width: 1.75rem; height: 1.75rem; margin-top: 0.15rem; color: var(--brand); }
.sync-page-title { margin: 0; font-size: 1.875rem; line-height: 2.25rem; font-weight: 800; letter-spacing: -0.025em; }
.sync-page-header p { margin: 0.25rem 0 0; color: var(--text-secondary); font-size: 0.875rem; }
@media (max-width: 640px) { .sync-page { padding: 1rem; } }
</style>
