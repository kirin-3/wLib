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
import StepShell from "./StepShell.vue";

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
  void checkPairing();
  // Gaming Mode has no desktop browser, so the step shows no install steps
  // and does not poll. The pairing event still updates a paired state.
  if (gamingMode.value) return;
  void loadPlatformCapabilities().then((caps) => {
    if (!disposed) platformCapabilities.value = caps;
  });
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
  <StepShell
    :icon="IconPuzzle"
    title="Browser extension"
    lead="Adds “Add to wLib” buttons to F95Zone, LewdCorner, DLsite, VNDB, and Ryuugames pages, and enables in-app downloads."
  >
    <div v-if="!gamingMode || pairedLabel" class="status-row">
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

    <section
      v-if="gamingMode"
      class="gaming-card"
      aria-labelledby="extension-gaming-mode-title"
      data-testid="extension-gaming-mode-card"
    >
      <IconDeviceGamepad2 class="w-5 h-5 shrink-0 gaming-card-icon" aria-hidden="true" />
      <div class="min-w-0">
        <h3 id="extension-gaming-mode-title" class="gaming-card-title">
          {{ pairedLabel ? "Change it later in Desktop Mode" : "Set it up later in Desktop Mode" }}
        </h3>
        <p class="gaming-card-text">
          The extension runs in a desktop browser. Steam Gaming Mode has no
          desktop browser.
        </p>
        <p class="gaming-card-text">
          In Desktop Mode, open <strong class="gaming-card-strong">Extension</strong>
          in the wLib sidebar.
          {{ pairedLabel ? "You can add or remove a browser there." : "That page shows the steps." }}
        </p>
      </div>
    </section>

    <ExtensionInstallSteps
      v-else
      :chrome-directory="chromeExtensionDirectory"
      :firefox-file="firefoxExtensionFile"
      compact
      show-open-button
      @open-folder="openExtensionFolder"
    />
  </StepShell>
</template>

<style scoped>
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

.motion-off .waiting-dot {
  animation: none;
}

@media (prefers-reduced-motion: reduce) {
  .waiting-dot {
    animation: none;
  }
}

.gaming-card {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  max-width: 38rem;
  padding: 1rem 1.125rem;
  border-radius: 0.75rem;
  background: var(--bg-raised);
  border: 1px solid var(--border);
}

.gaming-card-icon {
  margin-top: 0.1rem;
  color: var(--brand-text);
}

.gaming-card-title {
  font-size: 0.9375rem;
  line-height: 1.35;
  font-weight: 600;
  color: var(--text-primary);
}

.gaming-card-text {
  margin-top: 0.35rem;
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--text-secondary);
  text-wrap: pretty;
}

.gaming-card-strong {
  color: var(--text-primary);
  font-weight: 600;
}
</style>
