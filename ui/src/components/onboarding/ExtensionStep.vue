<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { IconDeviceGamepad2, IconPlugConnected, IconPuzzle } from "@tabler/icons-vue";
import {
  api,
  CONSERVATIVE_PLATFORM_CAPABILITIES,
  type PlatformCapabilities,
} from "../../services/api";
import { EXTENSION_PAIRING_EVENT } from "../../utils/extensionPairing";
import { loadPlatformCapabilities } from "../../utils/platformCapabilities";
import { gamingMode } from "../../utils/tvMode";
import { notifyError } from "../../utils/toast";
import { useEventListener } from "../../composables/useEventListener";
import { useOnboarding } from "../../composables/useOnboarding";
import ExtensionInstallSteps from "../ExtensionInstallSteps.vue";

const onboarding = useOnboarding();
const platformCapabilities = ref<PlatformCapabilities>({
  ...CONSERVATIVE_PLATFORM_CAPABILITIES,
});

const extensionPath = (child: string) => {
  const root = platformCapabilities.value.extension_dir;
  if (!root) return child;
  const separator = platformCapabilities.value.platform === "windows" ? "\\" : "/";
  const normalizedChild = child.replace(/[\\/]/g, separator);
  return `${root.replace(/[\\/]$/, "")}${separator}${normalizedChild}`;
};

const chromeExtensionDirectory = computed(() => extensionPath("chrome"));
const firefoxExtensionFile = computed(() => extensionPath("firefox/wLib.xpi"));

const pairedLabel = computed(() => {
  const unique = [...new Set(onboarding.pairedBrowsers.value.map((b) => b.trim()).filter(Boolean))];
  return unique.length > 0 ? `Paired with ${unique.join(", ")}` : "";
});

let pollTimer: ReturnType<typeof setInterval> | null = null;
let disposed = false;

const checkPairing = async () => {
  if (disposed) return;
  await onboarding.refreshPairing();
};

const openExtensionFolder = async () => {
  try {
    const res = await api.openExtensionFolder();
    if (res && res.success === false) {
      notifyError("Failed to open extension folder: " + (res.error || "Unknown error"));
    }
  } catch (error) {
    notifyError("Error opening extension folder: " + String(error));
  }
};

useEventListener(window, EXTENSION_PAIRING_EVENT, () => {
  void checkPairing();
});

onMounted(() => {
  void loadPlatformCapabilities().then((caps) => {
    if (!disposed) platformCapabilities.value = caps;
  });
  void checkPairing();
  pollTimer = setInterval(() => {
    void checkPairing();
  }, 2000);
});

onUnmounted(() => {
  disposed = true;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});
</script>

<template>
  <div class="step-shell">
    <header class="step-header">
      <div class="step-icon-tile" aria-hidden="true">
        <IconPuzzle class="w-6 h-6" />
      </div>
      <div class="min-w-0">
        <h2 id="onboarding-step-title" tabindex="-1" class="step-title">
          Browser extension
        </h2>
        <p class="step-lead">
          Adds “Add to wLib” buttons to F95Zone, LewdCorner, DLsite, VNDB, and
          Ryuugames pages, and enables in-app downloads.
        </p>
      </div>
    </header>

    <div class="status-row">
      <div
        class="status-pill"
        :class="pairedLabel ? 'status-pill--paired' : 'status-pill--waiting'"
        role="status"
        aria-live="polite"
        data-testid="extension-pairing-pill"
      >
        <IconPlugConnected v-if="pairedLabel" class="w-4 h-4 shrink-0" aria-hidden="true" />
        <span v-else class="waiting-dot" aria-hidden="true" />
        <span>{{ pairedLabel || "Waiting for the extension…" }}</span>
      </div>
    </div>

    <div v-if="gamingMode" class="gaming-note" role="status" data-testid="extension-gaming-mode-note">
      <IconDeviceGamepad2 class="w-4 h-4 shrink-0 mt-0.5 gaming-note-icon" aria-hidden="true" />
      <span>
        You are in Steam Gaming Mode. The browser extension is set up from a
        desktop browser later.
      </span>
    </div>

    <ExtensionInstallSteps
      :chrome-directory="chromeExtensionDirectory"
      :firefox-file="firefoxExtensionFile"
      compact
      show-open-button
      @open-folder="openExtensionFolder"
    />
  </div>
</template>

<style scoped>
.step-shell {
  display: flex;
  flex-direction: column;
  gap: 1.125rem;
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

.status-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.85rem;
  border-radius: 9999px;
  font-size: 0.8125rem;
  font-weight: 600;
  border: 1px solid var(--border);
}

.status-pill--waiting {
  background: var(--bg-raised);
  color: var(--text-secondary);
}

.status-pill--paired {
  background: var(--success-bg);
  border-color: var(--success-border);
  color: var(--success-text);
}

.waiting-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 9999px;
  background: var(--brand);
  animation: ext-wait-pulse 2s ease-in-out infinite;
}

@keyframes ext-wait-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.85); }
}

:global(.motion-off) .waiting-dot {
  animation: none;
}

@media (prefers-reduced-motion: reduce) {
  .waiting-dot {
    animation: none;
  }
}

.gaming-note {
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

.gaming-note-icon {
  color: var(--brand-text);
}
</style>
