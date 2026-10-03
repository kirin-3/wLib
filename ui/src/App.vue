<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import {
  IconBrandGithub,
  IconDatabaseExport,
  IconLayout2,
  IconMoon,
  IconPlayerTrackNext,
  IconPlayerTrackPrev,
  IconPuzzle,
  IconReload,
  IconSettings,
  IconSun,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "./services/api";
import { isNewerVersion } from "./utils/appVersion";
import { motionEnabled } from "./utils/motionPreference";
import { dismissToast, notify, toasts } from "./utils/toast";
import { hasAvailableUpdate } from "./utils/libraryGames";

interface ExtensionEventDetail {
  url: string;
  title?: string;
  version?: string;
  coverImage?: string;
  tags?: string[];
  rating?: string;
  developer?: string;
  engine?: string;
}

const router = useRouter();
const hasAppUpdate = ref(false);
const currentVersion = ref("");
const latestVersion = ref("");
const isDark = ref(true);
const isNavCollapsed = ref(false);
const navCollapsedStorageKey = "wlib-nav-collapsed";
const fadeTransitionName = computed(() => (motionEnabled.value ? "fade" : ""));
const availableUpdateCount = ref(0);
let updatePollInterval: ReturnType<typeof setInterval> | null = null;
let lastUpdateProgress = "";
let pollingUpdates = false;
let updateCountRequest = 0;

const refreshUpdateCount = async () => {
  const request = ++updateCountRequest;
  try {
    const games = await api.getGames();
    if (request !== updateCountRequest) return;
    availableUpdateCount.value = games.filter(hasAvailableUpdate).length;
  } catch (error) {
    console.error("Failed to refresh update count", error);
  }
};

const pollLibraryUpdates = async () => {
  if (pollingUpdates) return;
  pollingUpdates = true;
  try {
    const status = await api.getUpdateStatus();
    if (status.success === false) return;
    const progress = JSON.stringify([status.running, status.checked, status.total, status.results]);
    if (progress !== lastUpdateProgress) {
      lastUpdateProgress = progress;
      window.dispatchEvent(new Event("wlib-refresh-library"));
    }
  } catch (error) {
    console.error("Failed to refresh update progress", error);
  } finally {
    pollingUpdates = false;
  }
};

const toggleTheme = () => {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle("light", !isDark.value);
  localStorage.setItem("wlib-theme", isDark.value ? "dark" : "light");
};

const toggleNavCollapse = () => {
  isNavCollapsed.value = !isNavCollapsed.value;
  localStorage.setItem(
    navCollapsedStorageKey,
    isNavCollapsed.value ? "true" : "false",
  );
};

const handleExtensionAdd = (event: Event) => {
  const data = (event as CustomEvent<ExtensionEventDetail>).detail;
  if (data && data.url) {
    router.push({
      path: "/",
      query: {
        action: "import",
        f95url: data.url,
        title: data.title,
        version: data.version || "",
        coverImage: data.coverImage,
        tags: JSON.stringify(data.tags || []),
        rating: data.rating,
        developer: data.developer,
        engine: data.engine,
      },
    }).catch((err) => console.error("Router error:", err));
  }
};

const handleExtensionOpen = (event: Event) => {
  const data = (event as CustomEvent<ExtensionEventDetail>).detail;
  if (data && data.url) {
    router.push({
      path: "/",
      query: {
        action: "open",
        f95url: data.url,
      },
    }).catch((err) => console.error("Router error:", err));
  }
};

onMounted(() => {
  // Load saved theme
  const savedTheme = localStorage.getItem("wlib-theme");
  if (savedTheme === "light") {
    isDark.value = false;
    document.documentElement.classList.add("light");
  }

  const savedNavCollapsed = localStorage.getItem(navCollapsedStorageKey);
  if (savedNavCollapsed === "true" || savedNavCollapsed === "false") {
    isNavCollapsed.value = savedNavCollapsed === "true";
  }

  window.addEventListener("wlib-extension-add", handleExtensionAdd);
  window.addEventListener("wlib-extension-open", handleExtensionOpen);
  window.addEventListener("wlib-refresh-library", refreshUpdateCount);

  // Check for App Updates on Startup
  onWebviewReady(async () => {
    void refreshUpdateCount();
    updatePollInterval = setInterval(pollLibraryUpdates, 5000);
    try {
      const extensionSync = await api.getStartupExtensionSyncStatus();
      if (extensionSync?.success && extensionSync?.updated) {
        const version = extensionSync.installed_version || extensionSync.bundled_version;
        notify(
          version
            ? `Synced browser extension files to v${version}. Reload the browser addon to pick up the update.`
            : "Synced browser extension files. Reload the browser addon to pick up the update.",
          "info",
          "Extension Updated",
        );
      }

      const versionInfo = await api.get_app_version();
      currentVersion.value = versionInfo?.version || "";

      const release = await api.check_app_updates();
      if (release && release.success && release.version) {
        latestVersion.value = release.version;
        if (isNewerVersion(release.version, currentVersion.value)) {
          hasAppUpdate.value = true;
        }
      }
    } catch (e) {
      console.error("Failed to check for app updates globally", e);
    }

    // Run the scheduled (weekly/monthly) game update check regardless of the open view.
    try {
      await api.maybeAutoCheck();
    } catch (e) {
      console.error("Auto-check trigger failed", e);
    }
  });
});

onUnmounted(() => {
  updateCountRequest++;
  window.removeEventListener("wlib-extension-add", handleExtensionAdd);
  window.removeEventListener("wlib-extension-open", handleExtensionOpen);
  window.removeEventListener("wlib-refresh-library", refreshUpdateCount);
  if (updatePollInterval) clearInterval(updatePollInterval);
});
</script>

<template>
  <div class="app-shell flex h-screen w-screen overflow-hidden">
    <!-- Sidebar -->
    <aside
      :class="[
        isNavCollapsed ? 'w-16' : 'w-64',
        'flex flex-col justify-between shrink-0 collapse-width-transition',
      ]"
      style="
        background: var(--bg-surface);
        border-right: 1px solid var(--border);
      "
    >
      <div>
        <div :class="isNavCollapsed ? 'px-2 py-8' : 'px-6 py-8'">
          <div
            class="flex items-center"
            :class="isNavCollapsed ? 'justify-center' : 'gap-3'"
          >
            <img
              src="/icon.svg"
              alt="wLib Logo"
              class="w-8 h-8 drop-shadow-lg shrink-0"
            />
            <h1
              class="brand-gradient-text text-2xl font-extrabold bg-clip-text text-transparent whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
            >
              wLib
            </h1>
          </div>
          <p
            class="text-xs uppercase tracking-widest font-semibold whitespace-nowrap transition-opacity duration-150"
            :class="
              isNavCollapsed
                ? 'opacity-0 h-0 mt-0 overflow-hidden'
                : 'opacity-100 mt-1.5'
            "
            style="color: var(--text-muted)"
          >
            Game Manager
          </p>
        </div>

        <nav :class="isNavCollapsed ? 'px-2 space-y-1 mt-2' : 'px-4 space-y-1 mt-2'">
          <router-link
            to="/"
            :title="isNavCollapsed ? 'Library' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconLayout2 class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Library</span
            >
          </router-link>

          <router-link
            to="/updates"
            :title="`Updates${availableUpdateCount ? ` (${availableUpdateCount} available)` : ''}`"
            :aria-label="`Updates, ${availableUpdateCount} available`"
            :class="[
              'nav-link relative flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconReload class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Updates</span
            >
            <span
              v-if="availableUpdateCount"
              class="rounded-full px-1.5 min-w-5 text-center text-[10px] font-bold"
              :class="isNavCollapsed ? 'absolute -top-1 -right-1' : 'ml-auto'"
              style="background: var(--brand); color: var(--text-inverse)"
              aria-hidden="true"
              >{{ availableUpdateCount }}</span
            >
          </router-link>

          <router-link
            to="/import-export"
            :title="isNavCollapsed ? 'Import / Export' : ''"
            :aria-label="isNavCollapsed ? 'Import / Export' : undefined"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium w-full text-left',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconDatabaseExport class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Import / Export</span
            >
          </router-link>

          <router-link
            to="/extension"
            :title="isNavCollapsed ? 'Extension' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconPuzzle class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Extension</span
            >
          </router-link>

          <router-link
            to="/settings"
            :title="isNavCollapsed ? 'Settings' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconSettings class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Settings</span
            >
          </router-link>
        </nav>
      </div>

      <!-- Footer: Version, Theme Toggle & Repo -->
      <div
        class="p-5 flex items-center"
        :class="isNavCollapsed ? 'justify-center' : 'justify-between'"
        style="border-top: 1px solid var(--border)"
      >
        <router-link
          to="/updates"
          v-if="hasAppUpdate && !isNavCollapsed"
          class="text-xs font-mono font-bold px-2 py-0.5 rounded-md animate-pulse"
          style="
            background: var(--brand-glow-strong);
            color: var(--brand);
            border: 1px solid var(--brand-deep);
          "
          >{{ latestVersion }} ✨</router-link
        >
        <span
          v-else-if="!isNavCollapsed"
          class="text-xs font-mono font-semibold"
          style="color: var(--text-muted)"
          >{{ currentVersion }}</span
        >

        <div
          :class="[
            'flex',
            isNavCollapsed ? 'flex-col items-center gap-2' : 'items-center gap-2',
          ]"
        >
          <!-- Theme Toggle -->
          <button
            @click="toggleTheme"
            class="theme-toggle p-1.5 rounded-lg"
            :title="isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'"
          >
            <IconSun v-if="isDark" class="w-4 h-4" />
            <IconMoon v-else class="w-4 h-4" />
          </button>

          <!-- GitHub -->
          <a
            v-if="!isNavCollapsed"
            href="https://github.com/kirin-3/wLib"
            target="_blank"
            rel="noopener noreferrer"
            class="theme-toggle p-1.5 rounded-lg"
            title="View Source on GitHub"
          >
            <IconBrandGithub class="w-4 h-4" />
          </a>

          <button
            @click="toggleNavCollapse"
            class="theme-toggle p-1.5 rounded-lg text-base leading-none font-semibold"
            :title="isNavCollapsed ? 'Expand navigation' : 'Collapse navigation'"
          >
            <IconPlayerTrackNext v-if="isNavCollapsed" class="w-4 h-4" />
            <IconPlayerTrackPrev v-else class="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <main class="flex-1 overflow-y-auto relative">
      <transition-group
        :name="fadeTransitionName"
        tag="div"
        class="allow-text-selection fixed bottom-5 right-5 z-[60] flex w-full max-w-sm flex-col gap-2"
      >
        <div
          v-for="toast in toasts"
          :key="toast.id"
          :role="toast.type === 'error' ? 'alert' : 'status'"
          class="toast flex items-start gap-3 rounded-xl px-4 py-3 shadow-2xl backdrop-blur-sm"
          :class="`toast--${toast.type}`"
        >
          <div class="min-w-0 flex-1">
            <p v-if="toast.title" class="text-sm font-semibold">{{ toast.title }}</p>
            <p class="copyable-feedback text-xs leading-5" :class="toast.title ? 'mt-1' : ''">
              {{ toast.message }}
            </p>
          </div>
          <button
            @click="dismissToast(toast.id)"
            class="theme-toggle -mr-1 rounded-md p-1 shrink-0"
            title="Dismiss"
            aria-label="Dismiss notification"
          >
            <IconX class="w-4 h-4" />
          </button>
        </div>
      </transition-group>

      <router-view v-slot="{ Component }">
        <transition :name="fadeTransitionName" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.toast {
  background: color-mix(in srgb, var(--bg-surface) 88%, var(--brand) 12%);
  border: 1px solid var(--brand-deep);
  color: var(--text-primary);
}
.toast--success {
  background: color-mix(in srgb, var(--bg-surface) 85%, var(--success-text) 15%);
  border-color: var(--success-border);
}
.toast--error {
  background: color-mix(in srgb, var(--bg-surface) 85%, var(--danger-text) 15%);
  border-color: var(--danger-border);
  color: var(--danger-text);
}

.app-shell {
  background: var(--bg-base);
  color: var(--text-primary);
}

.brand-gradient-text {
  background-image: linear-gradient(to right, var(--brand), var(--brand-deep));
}

.nav-link {
  color: var(--text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease;
}
.nav-link:hover {
  background: var(--bg-raised);
  color: var(--text-primary);
}
.nav-active {
  background: var(--brand-glow) !important;
  color: var(--brand) !important;
  font-weight: 600;
}

.theme-toggle {
  color: var(--text-muted);
  transition: background-color 0.15s ease, color 0.15s ease;
}
.theme-toggle:hover {
  color: var(--brand);
  background: var(--bg-raised);
}
</style>
