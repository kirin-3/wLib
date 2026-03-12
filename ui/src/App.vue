<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import {
  IconBrandGithub,
  IconLayout2,
  IconMoon,
  IconPlayerTrackNext,
  IconPlayerTrackPrev,
  IconPuzzle,
  IconReload,
  IconSettings,
  IconSun,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "./services/api";

interface StartupToast {
  visible: boolean;
  title: string;
  message: string;
}

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
const startupToast = ref<StartupToast>({ visible: false, title: "", message: "" });
const navCollapsedStorageKey = "wlib-nav-collapsed";
let startupToastTimeout: ReturnType<typeof setTimeout> | null = null;

const showStartupToast = (title: string, message: string) => {
  startupToast.value = { visible: true, title, message };
  if (startupToastTimeout) {
    clearTimeout(startupToastTimeout);
  }
  startupToastTimeout = setTimeout(() => {
    startupToast.value = { visible: false, title: "", message: "" };
  }, 5000);
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

  // Check for App Updates on Startup
  onWebviewReady(async () => {
    try {
      const extensionSync = await api.getStartupExtensionSyncStatus();
      if (extensionSync?.success && extensionSync?.updated) {
        const version = extensionSync.installed_version || extensionSync.bundled_version;
        showStartupToast(
          "Extension Updated",
          version
            ? `Synced browser extension files to v${version}. Reload the browser addon to pick up the update.`
            : "Synced browser extension files. Reload the browser addon to pick up the update.",
        );
      }

      const versionInfo = await api.get_app_version();
      currentVersion.value = versionInfo?.version || "";

      const release = await api.check_app_updates();
      if (release && release.success && release.version) {
        latestVersion.value = release.version;
        if (release.version !== currentVersion.value) {
          hasAppUpdate.value = true;
        }
      }
    } catch (e) {
      console.error("Failed to check for app updates globally", e);
    }
  });
});

onUnmounted(() => {
  window.removeEventListener("wlib-extension-add", handleExtensionAdd);
  window.removeEventListener("wlib-extension-open", handleExtensionOpen);
  if (startupToastTimeout) {
    clearTimeout(startupToastTimeout);
  }
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
            :title="isNavCollapsed ? 'Updates' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
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
      <transition name="fade">
        <div
          v-if="startupToast.visible"
          class="fixed top-5 right-5 z-50 max-w-sm rounded-xl px-4 py-3 shadow-2xl backdrop-blur-sm"
          style="
            background: color-mix(in srgb, var(--bg-surface) 88%, var(--brand) 12%);
            border: 1px solid var(--brand-deep);
            color: var(--text-primary);
          "
        >
          <p class="text-sm font-semibold" style="color: var(--text-primary)">
            {{ startupToast.title }}
          </p>
          <p class="mt-1 text-xs leading-5" style="color: var(--text-secondary)">
            {{ startupToast.message }}
          </p>
        </div>
      </transition>

      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
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

.app-shell {
  background: var(--bg-base);
  color: var(--text-primary);
}

.brand-gradient-text {
  background-image: linear-gradient(to right, var(--brand), var(--brand-deep));
}

.nav-link {
  color: var(--text-secondary);
  transition: all 0.15s ease;
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
  transition: all 0.2s ease;
}
.theme-toggle:hover {
  color: var(--brand);
  background: var(--bg-raised);
}
</style>
