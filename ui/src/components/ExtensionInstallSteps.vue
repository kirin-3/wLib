<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { IconFolderOpen } from "@tabler/icons-vue";

const props = withDefaults(
  defineProps<{
    chromeDirectory: string;
    firefoxFile: string;
    compact?: boolean;
    showOpenButton?: boolean;
  }>(),
  {
    compact: false,
    showOpenButton: false,
  },
);

const emit = defineEmits<{
  openFolder: [];
}>();
</script>

<template>
  <div v-if="props.compact" class="ext-install-grid">
    <div class="ext-install-card">
      <div class="ext-install-head">
        <span class="ext-badge ext-badge--chromium">Chrome / Chromium / Edge</span>
      </div>
      <ol class="ext-install-list">
        <li>
          Open <code class="ext-code">chrome://extensions</code> and turn on
          <strong class="ext-strong">Developer mode</strong>.
        </li>
        <li>
          Click <strong class="ext-strong">Load unpacked</strong> and pick
          <code class="ext-code ext-code--path">{{ props.chromeDirectory }}</code>.
        </li>
        <li>
          Open an F95Zone page and allow the pairing prompt in wLib.
        </li>
      </ol>
      <button
        v-if="props.showOpenButton"
        type="button"
        class="ui-card-btn ext-folder-btn"
        @click="emit('openFolder')"
      >
        <IconFolderOpen class="w-4 h-4 shrink-0" aria-hidden="true" />
        Open extension folder
      </button>
    </div>

    <div class="ext-install-card">
      <div class="ext-install-head">
        <span class="ext-badge ext-badge--firefox">Firefox</span>
      </div>
      <ol class="ext-install-list">
        <li>
          Open <strong class="ext-strong">Add-ons and themes</strong> and click the gear icon.
        </li>
        <li>
          Choose <strong class="ext-strong">Install Add-on From File…</strong> and pick
          <code class="ext-code ext-code--path">{{ props.firefoxFile }}</code>.
        </li>
        <li>
          Confirm the install and allow the pairing prompt in wLib.
        </li>
      </ol>
      <button
        v-if="props.showOpenButton"
        type="button"
        class="ui-card-btn ext-folder-btn"
        @click="emit('openFolder')"
      >
        <IconFolderOpen class="w-4 h-4 shrink-0" aria-hidden="true" />
        Open extension folder
      </button>
    </div>
  </div>

  <div v-else>
    <!-- Chrome / Chromium -->
    <div class="mb-6">
      <h4
        class="text-sm font-bold mb-3 flex items-center gap-2"
        style="color: var(--text-primary)"
      >
        <span
          class="bg-blue-600/20 text-blue-400 px-2 py-0.5 rounded text-xs font-bold"
          >Chrome / Chromium / Edge</span
        >
      </h4>
      <ol
        class="space-y-2 text-sm list-decimal list-inside"
        style="color: var(--text-secondary)"
      >
        <li>
          Open
          <code class="ext-code text-blue-400">chrome://extensions</code> in
          your browser
        </li>
        <li>
          Enable
          <span class="font-medium" style="color: var(--text-primary)"
            >Developer mode</span
          >
          (toggle in the top-right corner)
        </li>
        <li>
          Click
          <span class="font-medium" style="color: var(--text-primary)"
            >Load unpacked</span
          >
        </li>
        <li>
          Select the
          <code class="ext-code text-blue-400">{{
            props.chromeDirectory
          }}</code>
          folder
        </li>
        <li>The wLib extension icon should appear in your toolbar</li>
      </ol>
      <p class="text-xs mt-3 italic" style="color: var(--text-muted)">
        Note: the extension reloads itself on your next F95Zone page
        after wLib syncs a new version. If you loaded it before
        automatic updates existed, restart your Chromium browser once.
      </p>
    </div>

    <!-- Firefox -->
    <div>
      <h4
        class="text-sm font-bold mb-3 flex items-center gap-2"
        style="color: var(--text-primary)"
      >
        <span
          class="bg-orange-600/20 text-orange-400 px-2 py-0.5 rounded text-xs font-bold"
          >Firefox</span
        >
      </h4>
      <ol
        class="space-y-2 text-sm list-decimal list-inside"
        style="color: var(--text-secondary)"
      >
        <li>
          Open Firefox
          <span class="font-medium" style="color: var(--text-primary)"
            >Add-ons and themes</span
          >
        </li>
        <li>
          Click the gear icon and choose
          <span class="font-medium" style="color: var(--text-primary)"
            >Install Add-on From File...</span
          >
        </li>
        <li>
          Select
          <code class="ext-code text-orange-400">{{
            props.firefoxFile
          }}</code>
          directly
        </li>
        <li>Confirm the installation when Firefox prompts you</li>
      </ol>
      <p class="text-xs mt-3 italic" style="color: var(--text-muted)">
        Note: Firefox updates the add-on automatically after
        installation. If you installed it before automatic updates
        existed, install the XPI once more. Release builds include a
        signed XPI for normal Firefox installation.
      </p>
    </div>
  </div>
</template>

<style scoped>
.ext-install-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.875rem;
}

@media (max-width: 44rem) {
  .ext-install-grid {
    grid-template-columns: 1fr;
  }
}

.ext-install-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem;
}

.ext-install-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.ext-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.55rem;
  border-radius: 0.375rem;
  font-size: 0.75rem;
  font-weight: 700;
  border: 1px solid transparent;
}

.ext-badge--chromium {
  background: var(--tag-blue-bg);
  color: var(--tag-blue-text);
  border-color: var(--tag-blue-border);
}

.ext-badge--firefox {
  background: var(--tag-orange-bg);
  color: var(--tag-orange-text);
  border-color: var(--tag-orange-border);
}

.ext-install-list {
  margin: 0;
  padding-left: 1.15rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--text-secondary);
  list-style: decimal;
}

.ext-strong {
  color: var(--text-primary);
  font-weight: 600;
}

.ext-code {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  color: var(--text-primary);
}

.ext-code--path {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.ext-folder-btn {
  margin-top: auto;
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
</style>
