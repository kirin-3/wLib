<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import {
  IconBrandGithub,
  IconCloud,
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
import { api, onWebviewReady, type DownloadOffer, type GameRecord } from "./services/api";
import { isNewerVersion } from "./utils/appVersion";
import { motionEnabled } from "./utils/motionPreference";
import { isDarkTheme, setThemePreference } from "./utils/themePreference";
import { dismissToast, notify, toasts } from "./utils/toast";
import { hasAvailableUpdate } from "./utils/libraryGames";
import {
  INSTALL_ARCHIVE_EVENT,
  type InstallArchiveRequest,
} from "./utils/installArchive";
import InstallArchiveModal from "./components/modals/InstallArchiveModal.vue";

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
const mainRef = ref<HTMLElement | null>(null);
const hasAppUpdate = ref(false);
const currentVersion = ref("");
const latestVersion = ref("");
const navCollapsedStorageKey = "wlib-nav-collapsed";
const getInitialNavCollapsed = (): boolean => {
  try {
    return localStorage.getItem(navCollapsedStorageKey) === "true";
  } catch (error) {
    console.warn("Could not read nav state", error);
    return false;
  }
};
const isNavCollapsed = ref(getInitialNavCollapsed());
const fadeTransitionName = computed(() => (motionEnabled.value ? "fade" : ""));
const availableUpdateCount = ref(0);
let updatePollInterval: ReturnType<typeof setInterval> | null = null;
let lastUpdateProgress = "";
let pollingUpdates = false;
let updateCountRequest = 0;
let wasUpdateRunning = false;
const notifiedFinishedIds = new Set<number>();

// Install-from-archive dialog (global so any view or toast can open it).
const showInstallModal = ref(false);
const installGame = ref<GameRecord | null>(null);
const installArchivePath = ref("");

const handleInstallArchiveRequest = async (event: Event) => {
  const detail = (event as CustomEvent<InstallArchiveRequest>).detail || {};
  installArchivePath.value = detail.archivePath || "";
  installGame.value = null;
  if (detail.gameId != null) {
    try {
      const games = await api.getGames();
      installGame.value = games.find((game) => game.id === detail.gameId) || null;
    } catch (error) {
      console.error("Failed to resolve game for install dialog", error);
    }
  }
  showInstallModal.value = true;
};

// Downloads-folder scan (throttled, no background watcher).
let lastDownloadScan = 0;
const shownOfferKeys = new Set<string>();
const offerToastByKey = new Map<string, number>();

const dismissOfferToast = (toastId: number) => {
  for (const [key, id] of offerToastByKey) {
    if (id === toastId) {
      offerToastByKey.delete(key);
      void api.dismissDownloadOffer(key);
      break;
    }
  }
  dismissToast(toastId);
};

const scanDownloadOffers = async () => {
  const now = Date.now();
  if (now - lastDownloadScan < 60_000) return;
  lastDownloadScan = now;
  let offers: DownloadOffer[] = [];
  try {
    const result = await api.findDownloadOffers();
    if (!result?.success) return;
    offers = result.offers || [];
  } catch (error) {
    console.error("Failed to scan Downloads folder", error);
    return;
  }
  for (const offer of offers) {
    if (shownOfferKeys.has(offer.key)) continue;
    shownOfferKeys.add(offer.key);
    notify(
      `${offer.game_title}: version ${offer.archive_version || offer.latest_version} is waiting in your Downloads folder.`,
      "info",
      "Update downloaded",
      {
        label: "Install update",
        run: () => {
          offerToastByKey.delete(offer.key);
          window.dispatchEvent(
            new CustomEvent<InstallArchiveRequest>(INSTALL_ARCHIVE_EVENT, {
              detail: { gameId: offer.game_id, archivePath: offer.archive_path },
            }),
          );
        },
      },
    );
    const toast = toasts[toasts.length - 1];
    if (toast) offerToastByKey.set(offer.key, toast.id);
  }
};

const handleWindowFocus = () => {
  void scanDownloadOffers();
};

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
    if (status.running && !wasUpdateRunning) notifiedFinishedIds.clear();
    for (const result of status.results) {
      if (result.newly_finished && !notifiedFinishedIds.has(result.id)) {
        notifiedFinishedIds.add(result.id);
        notify(`${result.title} is finished`, "success");
      }
    }
    const progress = JSON.stringify([status.running, status.checked, status.total, status.results]);
    if (progress !== lastUpdateProgress) {
      lastUpdateProgress = progress;
      window.dispatchEvent(new Event("wlib-refresh-library"));
    }
    if (wasUpdateRunning && !status.running) void scanDownloadOffers();
    wasUpdateRunning = !!status.running;
  } catch (error) {
    console.error("Failed to refresh update progress", error);
  } finally {
    pollingUpdates = false;
  }
};

// Ctrl+N opens Add Game from any view; LibraryView handles ?action=add.
const handleGlobalShortcut = (event: KeyboardEvent) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
  if (event.key.toLowerCase() !== "n" || document.querySelector('[aria-modal="true"]')) return;
  event.preventDefault();
  void router.push({ path: "/", query: { action: "add" } });
};

const toggleTheme = () => setThemePreference(isDarkTheme.value ? "light" : "dark");

const toggleNavCollapse = () => {
  isNavCollapsed.value = !isNavCollapsed.value;
  try {
    localStorage.setItem(
      navCollapsedStorageKey,
      isNavCollapsed.value ? "true" : "false",
    );
  } catch (error) {
    console.warn("Could not save nav state", error);
  }
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
  window.addEventListener("keydown", handleGlobalShortcut);
  window.addEventListener("wlib-extension-add", handleExtensionAdd);
  window.addEventListener("wlib-extension-open", handleExtensionOpen);
  window.addEventListener("wlib-refresh-library", refreshUpdateCount);
  window.addEventListener(INSTALL_ARCHIVE_EVENT, handleInstallArchiveRequest);

  // Check for App Updates on Startup
  onWebviewReady(async () => {
    void refreshUpdateCount();
    void scanDownloadOffers();
    // Listen only once the backend is injected: a focus event at startup would
    // otherwise scan the mocks and the 60 s throttle would skip this real scan.
    window.addEventListener("focus", handleWindowFocus);
    updatePollInterval = setInterval(pollLibraryUpdates, 5000);
    try {
      const extensionSync = await api.getStartupExtensionSyncStatus();
      if (extensionSync?.success && extensionSync?.updated) {
        const version = extensionSync.installed_version || extensionSync.bundled_version;
        notify(
          version
            ? `Browser extension updated to v${version}. Chromium browsers apply it on your next F95Zone page; Firefox updates automatically.`
            : "Browser extension updated. Chromium browsers apply it on your next F95Zone page; Firefox updates automatically.",
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
  window.removeEventListener("keydown", handleGlobalShortcut);
  window.removeEventListener("wlib-extension-add", handleExtensionAdd);
  window.removeEventListener("wlib-extension-open", handleExtensionOpen);
  window.removeEventListener("wlib-refresh-library", refreshUpdateCount);
  window.removeEventListener(INSTALL_ARCHIVE_EVENT, handleInstallArchiveRequest);
  window.removeEventListener("focus", handleWindowFocus);
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
              class="rounded-full px-1.5 min-w-5 text-center text-[0.625rem] font-bold"
              :class="isNavCollapsed ? 'absolute -top-1 -right-1' : 'ml-auto'"
              style="background: var(--brand); color: var(--on-brand)"
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
            to="/sync"
            :title="isNavCollapsed ? 'Sync' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              isNavCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconCloud class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="isNavCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Sync</span
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
            :title="isDarkTheme ? 'Switch to Light Mode' : 'Switch to Dark Mode'"
          >
            <IconSun v-if="isDarkTheme" class="w-4 h-4" />
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

          <!-- Buy Me a Coffee -->
          <a
            v-if="!isNavCollapsed"
            href="https://buymeacoffee.com/thekirin"
            target="_blank"
            rel="noopener noreferrer"
            class="theme-toggle p-1.5 rounded-lg"
            title="Buy Me a Coffee"
          >
            <!-- Buy Me a Coffee logo from Simple Icons (CC0) -->
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M20.216 6.415l-.132-.666c-.119-.598-.388-1.163-1.001-1.379-.197-.069-.42-.098-.57-.241-.152-.143-.196-.366-.231-.572-.065-.378-.125-.756-.192-1.133-.057-.325-.102-.69-.25-.987-.195-.4-.597-.634-.996-.788a5.723 5.723 0 00-.626-.194c-1-.263-2.05-.36-3.077-.416a25.834 25.834 0 00-3.7.062c-.915.083-1.88.184-2.75.5-.318.116-.646.256-.888.501-.297.302-.393.77-.177 1.146.154.267.415.456.692.58.36.162.737.284 1.123.366 1.075.238 2.189.331 3.287.37 1.218.05 2.437.01 3.65-.118.299-.033.598-.073.896-.119.352-.054.578-.513.474-.834-.124-.383-.457-.531-.834-.473-.466.074-.96.108-1.382.146-1.177.08-2.358.082-3.536.006a22.228 22.228 0 01-1.157-.107c-.086-.01-.18-.025-.258-.036-.243-.036-.484-.08-.724-.13-.111-.027-.111-.185 0-.212h.005c.277-.06.557-.108.838-.147h.002c.131-.009.263-.032.394-.048a25.076 25.076 0 013.426-.12c.674.019 1.347.067 2.017.144l.228.031c.267.04.533.088.798.145.392.085.895.113 1.07.542.055.137.08.288.111.431l.319 1.484a.237.237 0 01-.199.284h-.003c-.037.006-.075.01-.112.015a36.704 36.704 0 01-4.743.295 37.059 37.059 0 01-4.699-.304c-.14-.017-.293-.042-.417-.06-.326-.048-.649-.108-.973-.161-.393-.065-.768-.032-1.123.161-.29.16-.527.404-.675.701-.154.316-.199.66-.267 1-.069.34-.176.707-.135 1.056.087.753.613 1.365 1.37 1.502a39.69 39.69 0 0011.343.376.483.483 0 01.535.53l-.071.697-1.018 9.907c-.041.41-.047.832-.125 1.237-.122.637-.553 1.028-1.182 1.171-.577.131-1.165.2-1.756.205-.656.004-1.31-.025-1.966-.022-.699.004-1.556-.06-2.095-.58-.475-.458-.54-1.174-.605-1.793l-.731-7.013-.322-3.094c-.037-.351-.286-.695-.678-.678-.336.015-.718.3-.678.679l.228 2.185.949 9.112c.147 1.344 1.174 2.068 2.446 2.272.742.12 1.503.144 2.257.156.966.016 1.942.053 2.892-.122 1.408-.258 2.465-1.198 2.616-2.657.34-3.332.683-6.663 1.024-9.995l.215-2.087a.484.484 0 01.39-.426c.402-.078.787-.212 1.074-.518.455-.488.546-1.124.385-1.766zm-1.478.772c-.145.137-.363.201-.578.233-2.416.359-4.866.54-7.308.46-1.748-.06-3.477-.254-5.207-.498-.17-.024-.353-.055-.47-.18-.22-.236-.111-.71-.054-.995.052-.26.152-.609.463-.646.484-.057 1.046.148 1.526.22.577.088 1.156.159 1.737.212 2.48.226 5.002.19 7.472-.14.45-.06.899-.13 1.345-.21.399-.072.84-.206 1.08.206.166.281.188.657.162.974a.544.544 0 01-.169.364zm-6.159 3.9c-.862.37-1.84.788-3.109.788a5.884 5.884 0 01-1.569-.217l.877 9.004c.065.78.717 1.38 1.5 1.38 0 0 1.243.065 1.658.065.447 0 1.786-.065 1.786-.065.783 0 1.434-.6 1.499-1.38l.94-9.95a3.996 3.996 0 00-1.322-.238c-.826 0-1.491.284-2.26.613z" />
            </svg>
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
    <main ref="mainRef" class="flex-1 overflow-y-auto relative">
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
            <button
              v-if="toast.action"
              @click="dismissToast(toast.id); toast.action.run()"
              class="toast-action mt-2 px-3 py-1.5 rounded-lg text-xs font-bold"
            >
              {{ toast.action.label }}
            </button>
          </div>
          <button
            @click="dismissOfferToast(toast.id)"
            class="theme-toggle -mr-1 rounded-md p-1 shrink-0"
            title="Dismiss"
            aria-label="Dismiss notification"
          >
            <IconX class="w-4 h-4" />
          </button>
        </div>
      </transition-group>

      <router-view v-slot="{ Component }">
        <transition
          :name="fadeTransitionName"
          mode="out-in"
          @before-enter="mainRef?.scrollTo(0, 0)"
        >
          <component :is="Component" />
        </transition>
      </router-view>

      <InstallArchiveModal
        v-model="showInstallModal"
        :game="installGame"
        :preselected-archive="installArchivePath"
      />
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

.toast-action {
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
}
.toast-action:hover {
  filter: brightness(1.1);
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
