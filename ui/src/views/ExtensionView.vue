<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, onMounted, onUnmounted, useTemplateRef, watch } from "vue";
import { useRoute } from "vue-router";
import {
  IconActivityHeartbeat,
  IconFolderOpen,
  IconHelpCircle,
  IconInfoCircle,
  IconPuzzle,
  IconRefresh,
} from "@tabler/icons-vue";
import {
  api,
  CONSERVATIVE_PLATFORM_CAPABILITIES,
  onWebviewReady,
  type ExtensionPairingEntry,
  type PlatformCapabilities,
} from "../services/api";
import { confirmAction } from "../utils/confirm";
import ExtensionInstallSteps from "../components/ExtensionInstallSteps.vue";
import { EXTENSION_SITE_SWITCHES, SITE_SETTINGS_HASH, SITE_SETTINGS_ID } from "../utils/extensionSiteSettings";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { notifyError } from "../utils/toast";

type ConnectionStatus = "checking" | "connected" | "disconnected";

const connectionStatus = ref<ConnectionStatus>("checking");
const lastCheck = ref("");
const platformCapabilities = ref<PlatformCapabilities>({
  ...CONSERVATIVE_PLATFORM_CAPABILITIES,
});
const extensionDirectory = computed(
  () => platformCapabilities.value.extension_dir || "the wLib extension data folder",
);
const extensionPath = (child: string) => {
  const root = platformCapabilities.value.extension_dir;
  if (!root) return child;
  const separator = platformCapabilities.value.platform === "windows" ? "\\" : "/";
  const normalizedChild = child.replace(/[\\/]/g, separator);
  return `${root.replace(/[\\/]$/, "")}${separator}${normalizedChild}`;
};
const chromeExtensionDirectory = computed(() => extensionPath("chrome"));
const firefoxExtensionFile = computed(() => extensionPath("firefox/wLib.xpi"));
let pollInterval: ReturnType<typeof setInterval> | null = null;

const checkConnection = async () => {
  try {
    const resp = await api.getExtensionServiceStatus();
    if (resp && resp.reachable) {
      connectionStatus.value = "connected";
    } else {
      connectionStatus.value = "disconnected";
    }
  } catch {
    connectionStatus.value = "disconnected";
  }
  lastCheck.value = new Date().toLocaleTimeString();
  void loadPairedExtensions();
};

const pairedExtensions = ref<ExtensionPairingEntry[]>([]);
const route = useRoute();
const siteSettings = useTemplateRef<HTMLElement>("siteSettings");
let pairedListLoaded = false;
let pendingGuideScroll: { main: HTMLElement; top: number } | undefined;

const stopTrackingGuideScroll = () => {
  pendingGuideScroll?.main.removeEventListener("scroll", trackGuideScroll);
  pendingGuideScroll = undefined;
};
function trackGuideScroll() {
  if (pendingGuideScroll && Math.abs(pendingGuideScroll.main.scrollTop - pendingGuideScroll.top) > 1) {
    stopTrackingGuideScroll();
  }
}
const scrollToGuide = () => siteSettings.value?.scrollIntoView({ block: "start", behavior: "instant" });

const followSiteSettingsHash = async () => {
  stopTrackingGuideScroll();
  await nextTick();
  const main = siteSettings.value?.closest("main");
  if (!main || route.path !== "/extension") return;
  if (route.hash !== SITE_SETTINGS_HASH) {
    if (!route.hash) main.scrollTo({ top: 0, behavior: "instant" });
    return;
  }
  scrollToGuide();
  if (!pairedListLoaded) {
    pendingGuideScroll = { main, top: main.scrollTop };
    main.addEventListener("scroll", trackGuideScroll, { passive: true });
  }
};
watch(() => route.hash, followSiteSettingsHash);

const loadPairedExtensions = async () => {
  try {
    pairedExtensions.value = (await api.getExtensionPairing()).paired || [];
  } catch (error) {
    console.error("Could not load allowed extensions", error);
  }
  if (pairedListLoaded) return;
  pairedListLoaded = true;
  trackGuideScroll();
  const shouldRealign = !!pendingGuideScroll;
  stopTrackingGuideScroll();
  if (shouldRealign) {
    await nextTick();
    if (route.hash === SITE_SETTINGS_HASH) scrollToGuide();
  }
};

const removePairedExtension = async (entry: ExtensionPairingEntry) => {
  const confirmed = await confirmAction({
    title: "Remove browser extension",
    message: `Remove the ${entry.browser} extension with code ${entry.id}? It must ask again before it can connect.`,
    confirmLabel: "Remove",
    danger: true,
  });
  if (!confirmed) return;
  try {
    const result = await api.revokeExtensionPairing(entry.id);
    if (result.success === false) notifyError(result.error || "Could not remove the extension");
  } catch (error) {
    notifyError("Could not remove the extension: " + String(error));
  }
  await loadPairedExtensions();
};

const openExtensionFolder = async () => {
  try {
    const res = await api.openExtensionFolder();
    if (res && res.success === false) {
      notifyError("Failed to open extension folder: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Could not open extension folder via API", e);
    notifyError("Error opening extension folder: " + String(e));
  }
};

// The bridge and capability load can finish after the view is left; the poll
// must not start then, because nothing would ever clear it.
let unmounted = false;

onMounted(() => {
  void followSiteSettingsHash();
  onWebviewReady(async () => {
    if (unmounted) return;
    platformCapabilities.value = await loadPlatformCapabilities();
    if (unmounted) return;
    checkConnection();
    pollInterval = setInterval(checkConnection, 10000);
  });
});

onUnmounted(() => {
  unmounted = true;
  stopTrackingGuideScroll();
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = null;
});
</script>

<template>
  <div class="allow-text-selection mx-auto w-full p-8 max-w-4xl pb-12">
    <header class="mb-8">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconPuzzle class="ui-page-heading-icon" />
        <span>Browser Extension</span>
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Add games to wLib from F95Zone, LewdCorner, DLsite, VNDB and Ryuugames.
      </p>
    </header>

    <div class="space-y-8 mb-8">
      <!-- Local Service Status -->
      <section class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconActivityHeartbeat class="ui-section-icon" />
          Local Service Status
        </h3>

        <div class="flex items-center gap-4">
          <div class="flex items-center gap-3">
            <div
              :class="[
                'w-3 h-3 rounded-full',
                connectionStatus === 'connected'
                  ? 'bg-green-500 shadow-lg shadow-green-500/40'
                  : connectionStatus === 'checking'
                    ? 'bg-yellow-500 animate-pulse'
                    : 'bg-red-500 shadow-lg shadow-red-500/40',
              ]"
            ></div>

            <div>
              <p
                :class="[
                  'text-sm font-semibold',
                  connectionStatus === 'connected'
                    ? 'text-green-400'
                    : connectionStatus === 'checking'
                      ? 'text-yellow-400'
                      : 'text-red-400',
                ]"
              >
                {{
                  connectionStatus === "connected"
                    ? "Connected"
                    : connectionStatus === "checking"
                      ? "Checking..."
                      : "Disconnected"
                }}
              </p>
              <p class="text-xs" style="color: var(--text-muted)">
                Checks whether wLib's local HTTP service on port 8183 is reachable.
                It does not verify whether the browser addon is installed. Last checked:
                {{ lastCheck || "-" }}
              </p>
            </div>
          </div>

          <button @click="checkConnection" class="ext-btn ui-action-btn ml-auto">
            <IconRefresh class="ui-action-icon" />
            Refresh
          </button>
        </div>
      </section>

      <!-- Extension Folder -->
      <section class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconFolderOpen class="ui-section-icon" />
          Extension Files
        </h3>

        <p class="text-sm mb-4" style="color: var(--text-secondary)">
          The browser extension is synced into
          <code class="ext-code" style="color: var(--brand)">{{
            extensionDirectory
          }}</code>
          on app startup and again when you use
          <span style="color: var(--text-primary)">Open Extension Folder</span>.
          Use the synced files there, not the repo-root
          <code class="ext-code">extension/</code> folder.
        </p>

        <button
          @click="openExtensionFolder"
          class="ui-action-btn px-5 py-2.5 rounded-lg text-sm font-medium transition-all"
          style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
        >
          <IconFolderOpen class="ui-action-icon" />
          Open Extension Folder
        </button>
      </section>

      <!-- Allowed browser extensions -->
      <section class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-2"
          style="color: var(--text-primary)"
        >
          <IconPuzzle class="ui-section-icon" />
          Allowed Browser Extensions
        </h3>
        <p class="text-sm mb-3" style="color: var(--text-secondary)">
          wLib asks you once before a browser extension can connect. The code
          matches the one on the extension's options page.
        </p>
        <p
          v-if="!pairedExtensions.length"
          class="text-sm"
          style="color: var(--text-muted)"
        >
          No browser extension is allowed yet.
        </p>
        <ul v-else class="space-y-2">
          <li
            v-for="entry in pairedExtensions"
            :key="entry.id"
            class="flex items-center gap-3 text-sm"
            style="color: var(--text-secondary)"
          >
            <span>{{ entry.browser }}</span>
            <code class="ext-code">{{ entry.id }}</code>
            <button
              class="ext-btn ui-action-btn ml-auto"
              @click="removePairedExtension(entry)"
            >
              Remove
            </button>
          </li>
        </ul>
      </section>

      <!-- Installation Instructions -->
      <section class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconInfoCircle class="ui-section-icon" />
          How to Install
        </h3>

        <ExtensionInstallSteps
          :chrome-directory="chromeExtensionDirectory"
          :firefox-file="firefoxExtensionFile"
        />
      </section>

    </div>

    <!-- Leave enough room below the anchor to align it on tall windows. -->
    <div class="space-y-8 pb-12" :class="{ 'min-h-[var(--app-vh)]': route.hash === SITE_SETTINGS_HASH }">
      <section :id="SITE_SETTINGS_ID" ref="siteSettings" class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconPuzzle class="ui-section-icon" />
          Turn on more sites
        </h3>
        <div class="space-y-4 text-sm" style="color: var(--text-secondary)">
          <p>
            F95Zone works right away. Other sites stay off until you turn them on in the extension settings.
            Your browser asks for permission once for each site.
          </p>
          <ol class="list-decimal pl-5 space-y-2">
            <li>
              Click the wLib icon in your browser toolbar to open the extension settings.
              You can also right-click the icon and choose <code class="ext-code">Options</code>
              in Chrome or <code class="ext-code">Manage Extension</code> then
              <code class="ext-code">Preferences</code> in Firefox.
            </li>
            <li>Turn on the switches for the sites and downloads you use.</li>
            <li>Click <code class="ext-code">Allow</code> when your browser asks for permission.</li>
          </ol>
          <dl class="space-y-2">
            <div v-for="site in EXTENSION_SITE_SWITCHES" :key="site.name" class="grid gap-1 sm:grid-cols-[11rem_1fr] sm:gap-4">
              <dt><code class="ext-code">{{ site.name }}</code></dt>
              <dd>{{ site.description }}</dd>
            </div>
          </dl>
          <p>The pairing code is on the same settings page.</p>
        </div>
      </section>

      <!-- How it works -->
      <section class="ext-card rounded-xl p-6">
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconHelpCircle class="ui-section-icon" />
          How It Works
        </h3>
        <div class="space-y-3 text-sm" style="color: var(--text-secondary)">
          <p>
            When you visit an F95Zone game thread, the extension automatically:
          </p>
          <ul class="space-y-1.5 list-disc list-inside ml-2">
            <li>
              Extracts the game title, version, developer, engine, cover image,
              tags, and rating
            </li>
            <li>
              Shows an
              <span class="text-green-400 font-medium">"Add to wLib"</span>
              button on the page
            </li>
            <li>
              If the game is already in your library, shows
              <span class="font-medium" style="color: var(--brand)"
                >"Open in wLib"</span
              >
              instead
            </li>
          </ul>
          <p class="text-xs mt-2" style="color: var(--text-muted)">
            Communication happens via a local HTTP server on
            <code class="ext-code" style="color: var(--brand)"
              >localhost:8183</code
            >. wLib must be running for the extension to work.
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ext-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.ext-btn {
  background: var(--bg-overlay);
  color: var(--text-primary);
  padding: 0.375rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  border: 1px solid var(--border-hover);
  transition: all 0.15s ease;
}
.ext-btn:hover {
  background: var(--border-hover);
}

.ext-code {
  /* Accent text is tested against the surface color, not the raised one. */
  background: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
}
</style>
