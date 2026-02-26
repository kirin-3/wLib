<script setup>
import { ref, onMounted, onUnmounted, computed } from "vue";
import { api, onWebviewReady } from "../services/api";
import { marked } from "marked";
import DOMPurify from "dompurify";

const games = ref([]);
const status = ref({
  running: false,
  total: 0,
  checked: 0,
  current: "",
  results: [],
});
const lastCheckTime = ref("");
const autoCheckFreq = ref("weekly");
const lastAutoCheck = ref("");
let pollInterval = null;

// App Update State
const appUpdate = ref(null);
const appUpdateLoading = ref(true);
const currentVersion = ref("");

const loadGames = async () => {
  try {
    const res = await api.getGames();
    if (res && res.success === false) {
      console.error("Failed to load games:", res.error);
      games.value = [];
    } else {
      games.value = res || [];
    }
  } catch (e) {
    console.error("Failed to load games", e);
  }
};

const loadAutoCheckSetting = async () => {
  try {
    const s = await api.getAutoCheckSetting();
    if (s && s.success === false) {
      console.error("Failed to load auto-check setting:", s.error);
    } else if (s) {
      autoCheckFreq.value = s.frequency || "weekly";
      lastAutoCheck.value = s.last_check || "";
    }
  } catch (e) {
    console.error("Failed to load auto-check setting", e);
  }
};

const setFrequency = async (freq) => {
  autoCheckFreq.value = freq;
  try {
    const res = await api.setAutoCheckSetting(freq);
    if (res && res.success === false) {
      alert("Failed to set frequency: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to set frequency", e);
    alert("Error setting frequency: " + e.toString());
  }
};

const startCheck = async () => {
  try {
    const result = await api.checkAllUpdates();
    if (result && result.success === false) {
      alert("Failed to start check: " + (result.error || "Unknown error"));
    } else if (result && result.success) {
      lastCheckTime.value = new Date().toLocaleTimeString();
      startPolling();
    }
  } catch (e) {
    console.error("Failed to start update check", e);
    alert("Error starting check: " + e.toString());
  }
};

const cancelCheck = async () => {
  try {
    const result = await api.cancelUpdateCheck();
    if (result && result.success === false) {
      alert("Failed to cancel check: " + (result.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to cancel", e);
    alert("Error cancelling check: " + e.toString());
  }
};

const pollStatus = async () => {
  try {
    const s = await api.getUpdateStatus();
    if (s && s.success === false) {
      console.error("Poll status failed:", s.error);
    } else if (s) {
      status.value = s;
      if (!s.running && pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
        await loadGames();
        await loadAutoCheckSetting();
      }
    }
  } catch (e) {
    console.error("Poll failed", e);
  }
};

const startPolling = () => {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollStatus, 2000);
  pollStatus();
};

const openInBrowser = async (url) => {
  if (url) await api.openInBrowser(url);
};

const progress = computed(() => {
  if (!status.value.total) return 0;
  return Math.round((status.value.checked / status.value.total) * 100);
});

const gamesWithUpdates = computed(() => {
  return status.value.results.filter((r) => r.has_update);
});

const gamesChecked = computed(() => {
  return status.value.results.filter((r) => !r.error);
});

const gamesWithErrors = computed(() => {
  return status.value.results.filter((r) => r.error);
});

const persistedUpdates = computed(() => {
  return games.value.filter(
    (g) =>
      g.latest_version &&
      g.version &&
      g.latest_version.trim() !== g.version.trim(),
  );
});

const formatLastCheck = computed(() => {
  if (!lastAutoCheck.value) return "Never";
  try {
    const d = new Date(lastAutoCheck.value);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString();
  } catch {
    return lastAutoCheck.value;
  }
});

onMounted(() => {
  onWebviewReady(async () => {
    try {
      appUpdateLoading.value = true;
      const versionInfo = await api.get_app_version();
      currentVersion.value = versionInfo?.version || "";

      const release = await api.check_app_updates();
      if (release && release.success && release.version) {
        if (release.version !== currentVersion.value) {
          appUpdate.value = {
            version: release.version,
            changelogHtml: DOMPurify.sanitize(
              marked.parse(release.changelog || "No changelog provided."),
            ),
            url: release.url,
            assets: release.assets || [],
          };
        }
      }
    } catch (e) {
      console.error("Failed to check app updates", e);
    } finally {
      appUpdateLoading.value = false;
    }

    await loadGames();
    await loadAutoCheckSetting();
    const s = await api.getUpdateStatus();
    if (s && s.running) {
      status.value = s;
      startPolling();
    } else if (s) {
      status.value = s;
    }
    try {
      const autoResult = await api.maybeAutoCheck();
      if (autoResult && autoResult.triggered) {
        startPolling();
      }
    } catch (e) {
      console.error("Auto-check trigger failed", e);
    }
  });
});

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval);
});
</script>

<template>
  <div class="p-8 max-w-4xl pb-12">
    <header class="mb-8">
      <h2
        class="text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        Updates
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Check F95Zone for new game versions.
      </p>
    </header>

    <div class="space-y-6">
      <!-- App Update Available (GitHub) -->
      <section
        v-if="appUpdate"
        class="rounded-xl p-6 shadow-lg relative overflow-hidden"
        style="
          background: linear-gradient(
            135deg,
            var(--bg-surface),
            var(--bg-inset)
          );
          border: 1px solid var(--brand-deep);
        "
      >
        <div
          class="absolute top-0 right-0 w-64 h-64 rounded-full blur-3xl -mr-20 -mt-20 pointer-events-none"
          style="background: var(--brand-glow)"
        ></div>

        <div class="flex items-start justify-between mb-4 relative z-10">
          <div>
            <h3
              class="text-xl font-bold flex items-center gap-2 mb-1"
              style="color: var(--text-primary)"
            >
              <svg
                class="w-6 h-6"
                style="color: var(--brand)"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"
                />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
              </svg>
              wLib Update Available
            </h3>
            <div class="flex items-center gap-3">
              <span
                class="text-xs font-mono px-2 py-0.5 rounded-md"
                style="
                  background: var(--bg-raised);
                  color: var(--text-muted);
                  border: 1px solid var(--border);
                "
                >Current: {{ currentVersion }}</span
              >
              <span class="text-xs" style="color: var(--text-muted)">→</span>
              <span
                class="text-xs font-mono px-2 py-0.5 rounded-md font-bold animate-pulse"
                style="
                  background: var(--brand-glow);
                  color: var(--brand);
                  border: 1px solid var(--brand-deep);
                "
                >New: {{ appUpdate.version }}</span
              >
            </div>
          </div>

          <div class="flex gap-2 shrink-0">
            <button
              @click="openInBrowser(appUpdate.url)"
              class="update-btn flex items-center gap-1.5"
            >
              <svg
                class="w-3.5 h-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"
                />
              </svg>
              Release Notes
            </button>
            <button
              v-for="asset in appUpdate.assets.filter(
                (a) =>
                  a.name.endsWith('.AppImage') || a.name.endsWith('.tar.gz'),
              )"
              :key="asset.name"
              @click="openInBrowser(asset.url)"
              class="text-white px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 shrink-0"
              style="background: var(--brand); box-shadow: var(--shadow-brand)"
            >
              <svg
                class="w-3.5 h-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"
                />
              </svg>
              {{ asset.name.endsWith(".AppImage") ? ".AppImage" : ".tar.gz" }}
            </button>
          </div>
        </div>

        <div
          class="changelog-wrapper rounded-lg p-5 text-sm relative z-10 max-h-64 overflow-y-auto wlib-changelog"
          style="color: var(--text-secondary)"
          v-html="appUpdate.changelogHtml"
        ></div>
      </section>

      <!-- F95Zone Game Auto-Check Setting -->
      <section class="section-card rounded-xl p-6">
        <div class="flex items-center justify-between mb-3">
          <h3
            class="text-lg font-semibold flex items-center gap-2"
            style="color: var(--text-primary)"
          >
            <svg
              class="w-5 h-5"
              style="color: var(--brand)"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            Automatic Checking
          </h3>
        </div>

        <div class="flex items-center gap-3 mb-3">
          <span class="text-sm" style="color: var(--text-secondary)"
            >Frequency:</span
          >
          <div
            class="flex items-center rounded-lg overflow-hidden"
            style="border: 1px solid var(--border)"
          >
            <button
              v-for="opt in [
                { key: 'weekly', label: 'Weekly' },
                { key: 'monthly', label: 'Monthly' },
                { key: 'off', label: 'Off' },
              ]"
              :key="opt.key"
              @click="setFrequency(opt.key)"
              class="px-3 py-1.5 text-xs font-medium transition-all"
              :style="
                autoCheckFreq === opt.key
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'background: var(--bg-surface); color: var(--text-muted)'
              "
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <p class="text-xs" style="color: var(--text-muted)">
          Last checked: {{ formatLastCheck }}
        </p>
      </section>

      <!-- Check All Button / Progress -->
      <section class="section-card rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <h3
            class="text-lg font-semibold flex items-center gap-2"
            style="color: var(--text-primary)"
          >
            <svg
              class="w-5 h-5 text-green-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"
              />
            </svg>
            Manual Check
          </h3>

          <div class="flex items-center gap-3">
            <button
              v-if="!status.running"
              @click="startCheck"
              class="bg-green-600 hover:bg-green-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-green-900/20 flex items-center gap-2"
            >
              <svg
                class="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"
                />
              </svg>
              Check All Games
            </button>
            <button
              v-else
              @click="cancelCheck"
              class="bg-red-600/20 hover:bg-red-600/30 text-red-400 px-5 py-2 rounded-lg text-sm font-bold transition-all border border-red-500/30 flex items-center gap-2"
            >
              <svg
                class="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
              Cancel
            </button>
          </div>
        </div>

        <!-- Progress Bar -->
        <div v-if="status.running || status.checked > 0" class="space-y-2">
          <div
            class="flex items-center justify-between text-xs"
            style="color: var(--text-secondary)"
          >
            <span v-if="status.running" class="flex items-center gap-2">
              <svg
                class="w-3 h-3 animate-spin text-green-400"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"
                />
              </svg>
              Checking: {{ status.current }}
            </span>
            <span v-else class="text-green-400">✓ Complete</span>
            <span>{{ status.checked }}/{{ status.total }}</span>
          </div>
          <div
            class="h-2 rounded-full overflow-hidden"
            style="background: var(--bg-raised)"
          >
            <div
              class="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-500"
              :style="{ width: progress + '%' }"
            ></div>
          </div>
        </div>

        <p
          v-if="!status.running && status.total === 0"
          class="text-sm"
          style="color: var(--text-muted)"
        >
          Click "Check All Games" to scan F95Zone for new versions. Games are
          checked one-by-one with a 15-second delay to avoid rate limiting.
        </p>
      </section>

      <!-- Updates Available -->
      <section
        v-if="persistedUpdates.length > 0"
        class="section-card rounded-xl p-6"
        style="border-color: rgba(34, 197, 94, 0.2)"
      >
        <h3
          class="text-lg font-semibold text-green-400 mb-4 flex items-center gap-2"
        >
          <svg
            class="w-5 h-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
          Updates Available ({{ persistedUpdates.length }})
        </h3>

        <TransitionGroup name="list" tag="div" class="space-y-2">
          <div
            v-for="game in persistedUpdates"
            :key="game.id"
            class="list-item flex items-center justify-between rounded-lg px-4 py-3"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <div class="flex items-center gap-3 min-w-0">
              <img
                v-if="game.cover_image_path"
                :src="game.cover_image_path"
                class="w-10 h-10 rounded-lg object-cover shrink-0"
              />
              <div
                class="w-6 h-6 rounded shrink-0 flex items-center justify-center"
                style="background: var(--bg-overlay)"
                v-else
              >
                <svg
                  class="w-3 h-3"
                  style="color: var(--text-muted)"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
                </svg>
              </div>
              <div class="min-w-0">
                <p
                  class="text-sm font-semibold truncate"
                  style="color: var(--text-primary)"
                >
                  {{ game.title }}
                </p>
                <p class="text-xs" style="color: var(--text-muted)">
                  {{ game.developer }}
                </p>
              </div>
            </div>
            <div class="flex items-center gap-3 shrink-0">
              <div class="text-right">
                <span
                  class="text-xs font-mono"
                  style="color: var(--text-muted)"
                  >{{ game.version }}</span
                >
                <span class="text-xs mx-1" style="color: var(--text-muted)"
                  >→</span
                >
                <span class="text-xs text-green-400 font-mono font-bold">{{
                  game.latest_version
                }}</span>
              </div>
              <button
                v-if="game.f95_url"
                @click="openInBrowser(game.f95_url)"
                class="p-1.5 rounded-lg transition-all"
                style="color: #b380cc"
                title="Open F95Zone thread"
              >
                <svg
                  class="w-4 h-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path
                    d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"
                  />
                </svg>
              </button>
            </div>
          </div>
        </TransitionGroup>
      </section>

      <!-- Check Results -->
      <section
        v-if="!status.running && status.results.length > 0"
        class="section-card rounded-xl p-6"
      >
        <h3
          class="text-lg font-semibold mb-4 flex items-center gap-2"
          style="color: var(--text-primary)"
        >
          <svg
            class="w-5 h-5"
            style="color: var(--brand)"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path
              d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
            />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" x2="8" y1="13" y2="13" />
            <line x1="16" x2="8" y1="17" y2="17" />
          </svg>
          Last Check Results
        </h3>

        <div class="grid grid-cols-3 gap-4 mb-4">
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <p class="text-2xl font-bold" style="color: var(--text-primary)">
              {{ gamesChecked.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Checked</p>
          </div>
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid rgba(34, 197, 94, 0.2);
            "
          >
            <p class="text-2xl font-bold text-green-400">
              {{ gamesWithUpdates.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Updates</p>
          </div>
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid rgba(239, 68, 68, 0.2);
            "
          >
            <p
              class="text-2xl font-bold"
              :class="gamesWithErrors.length ? 'text-red-400' : ''"
              :style="!gamesWithErrors.length ? 'color: var(--text-muted)' : ''"
            >
              {{ gamesWithErrors.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Errors</p>
          </div>
        </div>

        <div v-if="gamesWithErrors.length" class="space-y-1">
          <p class="text-xs mb-1" style="color: var(--text-muted)">
            Failed checks:
          </p>
          <div
            v-for="err in gamesWithErrors"
            :key="err.id"
            class="text-xs text-red-400/70 flex items-center gap-2"
          >
            <span style="color: var(--text-muted)">{{ err.title }}:</span>
            <span class="truncate">{{ err.error }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.section-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.update-btn {
  background: var(--bg-overlay);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  border: 1px solid var(--border);
  transition: all 0.15s ease;
}
.update-btn:hover {
  background: var(--border-hover);
}

.changelog-wrapper {
  background: var(--bg-inset);
  border: 1px solid var(--border);
}

.list-move,
.list-enter-active,
.list-leave-active {
  transition: all 0.4s ease;
}
.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}
.list-leave-active {
  position: absolute;
}

/* Markdown styling for the changelog */
:deep(.wlib-changelog h1),
:deep(.wlib-changelog h2),
:deep(.wlib-changelog h3) {
  color: var(--text-primary);
  font-weight: 600;
  margin-top: 1rem;
  margin-bottom: 0.5rem;
}
:deep(.wlib-changelog h2) {
  font-size: 1.1rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.25rem;
}
:deep(.wlib-changelog p) {
  margin-bottom: 0.75rem;
  line-height: 1.5;
}
:deep(.wlib-changelog ul) {
  list-style-type: disc;
  margin-left: 1.5rem;
  margin-bottom: 1rem;
}
:deep(.wlib-changelog li) {
  margin-bottom: 0.25rem;
}
:deep(.wlib-changelog a) {
  color: var(--brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}
:deep(.wlib-changelog a:hover) {
  filter: brightness(1.2);
}
:deep(.wlib-changelog code) {
  background: var(--bg-raised);
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  font-family: monospace;
  font-size: 0.85em;
}
:deep(.wlib-changelog pre) {
  background: var(--bg-inset);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  margin-bottom: 1rem;
  border: 1px solid var(--border);
}
:deep(.wlib-changelog pre code) {
  background: transparent;
  padding: 0;
}
</style>
