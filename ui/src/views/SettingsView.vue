<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from "vue";
import {
  IconAutomation,
  IconDeviceFloppyFilled,
  IconDownloadFilled,
  IconFolderOpen,
  IconLoader2,
  IconLogout2,
  IconSettings,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import {
  applyMotionPreference,
  motionEnabled,
  saveMotionPreference,
} from "../utils/motionPreference";
import type {
  InstallProgressStatus,
  SettingsResponse,
  SystemDepsCommandResponse,
} from "../services/api";

const protonPath = ref("");
const prefixPath = ref("");
const playwrightPath = ref("~/.cache/ms-playwright");
const enableLogging = ref(false);
const animationsEnabled = ref(motionEnabled.value);
const installingDeps = ref(false);
const installError = ref("");
const downloadingProton = ref(false);
const protonError = ref("");
const installingRtps = ref(false);
const rtpError = ref("");
const ceInstalled = ref(false);
const cePath = ref("");
const installingCe = ref(false);
const ceError = ref("");
const dllsInstalled = ref(false);
const rtpsInstalled = ref(false);
const depsProgress = ref<InstallProgressStatus>({
  running: false,
  done: 0,
  total: 0,
  current: "",
  error: "",
});
const rtpProgress = ref<InstallProgressStatus>({
  running: false,
  done: 0,
  total: 0,
  current: "",
  error: "",
});
const systemDeps = ref<SystemDepsCommandResponse | null>(null);
const copiedCommand = ref(false);
const openingLoginSession = ref(false);
const resettingSession = ref(false);
const sessionMessage = ref("");
const sessionError = ref("");
const saveMessage = ref("");
const saveError = ref("");
const settingsLoaded = ref(false);
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let statusRefreshTimer: ReturnType<typeof setTimeout> | null = null;

const applySettings = (data: SettingsResponse) => {
  protonPath.value = data.proton_path || "";
  prefixPath.value = data.wine_prefix_path || "";
  playwrightPath.value = data.playwright_browsers_path || "~/.cache/ms-playwright";
  enableLogging.value = !!data.enable_logging;
};

const loadSettings = async () => {
  try {
    const data = await api.getSettings();
    if (data) {
      applySettings(data);
    }

    animationsEnabled.value = motionEnabled.value;

    const ceCheck = await api.isCheatEngineInstalled();
    ceInstalled.value = !!ceCheck?.installed;
    cePath.value = ceCheck?.path || "";

    await pollInstallStatus();

    const sysDeps = await api.getSystemDepsCommand();
    if (sysDeps) systemDeps.value = sysDeps;
  } catch (e) {
    console.error("Failed to load settings", e);
  } finally {
    settingsLoaded.value = true;
  }
};

const pollInstallStatus = async () => {
  try {
    const s = await api.getInstallStatus(prefixPath.value, protonPath.value);
    if (s) {
      dllsInstalled.value = !!s.dlls_installed;
      rtpsInstalled.value = !!s.rtps_installed;
      if (s.deps) {
        depsProgress.value = s.deps;
        installingDeps.value = !!s.deps.running;
        installError.value = s.deps.error || "";
      }
      if (s.rtps) {
        rtpProgress.value = s.rtps;
        installingRtps.value = !!s.rtps.running;
        rtpError.value = s.rtps.error || "";
      }
    }
  } catch (e) {}
};

const browseProton = async () => {
  try {
    const p = await api.browseRunnerFile(protonPath.value || "");
    if (p) {
      protonPath.value = p;
    }
  } catch (e) {
    console.error("Browse proton error", e);
    alert("Error browsing file: " + String(e));
  }
};

const browsePrefix = async () => {
  try {
    const p = await api.browseDirectory(prefixPath.value || "");
    if (p) {
      prefixPath.value = p;
    }
  } catch (e) {
    console.error("Browse prefix error", e);
    alert("Error browsing directory: " + String(e));
  }
};

const downloadProton = async () => {
  downloadingProton.value = true;
  protonError.value = "";
  try {
    const result = await api.downloadProtonGe();
    if (result && result.success && result.path) {
      protonPath.value = result.path;
      await saveSettings();
    } else {
      protonError.value = result?.error || "Failed to download Proton.";
    }
  } catch (e) {
    protonError.value = String(e);
  } finally {
    downloadingProton.value = false;
  }
};

const installRtps = async () => {
  installingRtps.value = true;
  rtpError.value = "";
  try {
    const result = await api.installRpgmakerRtp(prefixPath.value, protonPath.value);
    if (result && result.success) {
      startPolling();
    } else {
      rtpError.value = result?.error || "Unknown error occurred";
      installingRtps.value = false;
    }
  } catch (e) {
    rtpError.value = String(e);
    installingRtps.value = false;
  }
};

const installDeps = async () => {
  installingDeps.value = true;
  installError.value = "";
  try {
    const result = await api.installRpgmakerDependencies(prefixPath.value, protonPath.value);
    if (result && result.success) {
      startPolling();
    } else {
      installError.value = result?.error || "Unknown error occurred";
      installingDeps.value = false;
    }
  } catch (e) {
    installError.value = String(e);
    installingDeps.value = false;
  }
};

const startPolling = () => {
  if (pollTimer) return;
  const tick = async () => {
    await pollInstallStatus();
    if (installingDeps.value || installingRtps.value) {
      pollTimer = setTimeout(tick, 2000);
    } else {
      pollTimer = null;
    }
  };
  pollTimer = setTimeout(tick, 2000);
};

const downloadCe = async () => {
  installingCe.value = true;
  ceError.value = "";
  try {
    const result = await api.downloadCheatEngine();
    if (result && result.success) {
      ceInstalled.value = true;
      cePath.value = result.path || "";
    } else {
      ceError.value = result?.error || "Unknown error occurred";
    }
  } catch (e) {
    ceError.value = String(e);
  } finally {
    installingCe.value = false;
  }
};

const copyDepsCommand = () => {
  if (!systemDeps.value) return;
  const text = systemDeps.value.command;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
  copiedCommand.value = true;
  setTimeout(() => (copiedCommand.value = false), 2000);
};

const openLoginSession = async () => {
  openingLoginSession.value = true;
  sessionError.value = "";
  sessionMessage.value =
    "Playwright login window opened. Complete login, then close the window to continue.";

  try {
    const result = await api.openScraperLoginSession();
    if (result && result.success) {
      sessionMessage.value =
        result.message ||
        "Login session closed. Future checks will reuse this session.";
    } else {
      sessionMessage.value = "";
      sessionError.value = result?.error || "Failed to open login session.";
    }
  } catch (e) {
    sessionMessage.value = "";
    sessionError.value = String(e);
  } finally {
    openingLoginSession.value = false;
  }
};

const resetSession = async () => {
  resettingSession.value = true;
  sessionError.value = "";
  sessionMessage.value = "";

  try {
    const result = await api.resetScraperSession();
    if (result && result.success) {
      sessionMessage.value =
        result.message || "Scraper browser session was reset successfully.";
    } else {
      sessionError.value = result?.error || "Failed to reset scraper session.";
    }
  } catch (e) {
    sessionError.value = String(e);
  } finally {
    resettingSession.value = false;
  }
};

onMounted(() => {
  onWebviewReady(() => {
    loadSettings();
  });
});

onUnmounted(() => {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (statusRefreshTimer) {
    clearTimeout(statusRefreshTimer);
    statusRefreshTimer = null;
  }
});

const saving = ref(false);

watch([protonPath, prefixPath, playwrightPath, enableLogging, animationsEnabled], () => {
  if (saving.value) return;
  saveMessage.value = "";
  saveError.value = "";
});

watch(animationsEnabled, (enabled) => {
  if (!settingsLoaded.value) return;
  applyMotionPreference(enabled);
});

watch([protonPath, prefixPath], () => {
  if (!settingsLoaded.value) return;
  if (pollTimer) return;
  if (statusRefreshTimer) clearTimeout(statusRefreshTimer);
  statusRefreshTimer = setTimeout(() => {
    statusRefreshTimer = null;
    void pollInstallStatus();
  }, 300);
});

const saveSettings = async () => {
  if (saving.value) return;

  saving.value = true;
  saveMessage.value = "";
  saveError.value = "";

  try {
    const res = await api.saveSettings({
      proton_path: protonPath.value,
      wine_prefix_path: prefixPath.value,
      playwright_browsers_path: playwrightPath.value,
      enable_logging: enableLogging.value,
    });

    if (!res || res.success === false) {
      saveError.value = "Failed to save settings: " + (res?.error || "Unknown error");
      return;
    }

    saveMotionPreference(animationsEnabled.value);

    const persistedSettings = await api.getSettings();
    if (!persistedSettings) {
      saveError.value = "Settings were saved but could not be reloaded.";
      return;
    }

    applySettings(persistedSettings);
    animationsEnabled.value = motionEnabled.value;
    await pollInstallStatus();
    saveMessage.value = "Settings saved.";
  } catch (e) {
    console.error("Failed to save settings", e);
    saveError.value = "Error saving settings: " + String(e);
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <div class="allow-text-selection p-8 max-w-4xl pb-12">
    <header class="mb-10">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconSettings class="ui-page-heading-icon" />
        <span>Settings</span>
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Configure launch paths and application behavior.
      </p>
    </header>

    <div class="settings-card rounded-xl shadow-lg overflow-hidden">
      <div class="p-8 space-y-8">
        <!-- Environment Settings -->
        <section>
          <h3
            class="ui-section-heading text-lg font-semibold mb-4"
            style="color: var(--text-primary)"
          >
            <IconAutomation class="ui-section-icon" />
            Proton & Wine Environment
          </h3>

          <div class="space-y-5">
            <div>
              <label
                class="block text-sm font-medium mb-1.5 flex justify-between items-center"
                style="color: var(--text-secondary)"
              >
                <span>Proton / Wine Executable Path</span>
                <button
                  @click="downloadProton"
                  :disabled="downloadingProton"
                  class="ui-action-btn text-xs font-medium disabled:opacity-50"
                  style="color: var(--brand)"
                >
                  <IconLoader2
                    v-if="downloadingProton"
                    class="ui-action-icon animate-spin"
                  />
                  <IconDownloadFilled
                    v-else
                    class="ui-action-icon"
                  />
                  {{
                    downloadingProton
                      ? "Downloading (Check terminal)..."
                      : "Auto Download Latest GE-Proton"
                  }}
                </button>
              </label>
              <div class="flex gap-3">
                <input
                  v-model="protonPath"
                  type="text"
                  placeholder="/usr/bin/wine or /path/to/GE-Proton/proton"
                  class="settings-input flex-1"
                />
                <button @click="browseProton" class="settings-btn ui-action-btn">
                  <IconFolderOpen class="ui-action-icon" />
                  Browse
                </button>
              </div>
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                Leave empty to use the system default `wine` command.
              </p>
              <p v-if="protonError" class="copyable-feedback text-xs text-red-400 mt-1">
                {{ protonError }}
              </p>
            </div>

            <div>
              <label
                class="block text-sm font-medium mb-1.5"
                style="color: var(--text-secondary)"
                >Default Wine Prefix Path (WINEPREFIX)</label
              >
              <div class="flex gap-3">
                <input
                  v-model="prefixPath"
                  type="text"
                  placeholder="Auto-managed (~/.local/share/wLib/prefix) if left empty"
                  class="settings-input flex-1"
                />
                <button @click="browsePrefix" class="settings-btn ui-action-btn">
                  <IconFolderOpen class="ui-action-icon" />
                  Browse
                </button>
              </div>
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                The location where game dependencies and save files will be
                isolated.
              </p>
            </div>

            <div>
              <label
                class="block text-sm font-medium mb-1.5"
                style="color: var(--text-secondary)"
                >Playwright Browsers Path</label
              >
              <input
                v-model="playwrightPath"
                type="text"
                placeholder="~/.cache/ms-playwright"
                class="settings-input w-full"
              />
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                This controls where Chromium is installed for scraping. Changes
                apply after restarting wLib.
              </p>
            </div>

            <div
              class="p-4 rounded-lg"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <h4 class="text-sm font-medium" style="color: var(--text-primary)">
                F95 Login Session
              </h4>
              <p class="text-xs mt-1" style="color: var(--text-muted)">
                Use Playwright's persistent browser profile for login-required
                threads.
              </p>
              <div class="flex flex-wrap gap-3 mt-3">
                <button
                  @click="openLoginSession"
                  :disabled="openingLoginSession || resettingSession"
                  class="settings-btn ui-action-btn disabled:opacity-50"
                >
                  <img src="/f95.png" alt="F95" class="ui-action-icon" />
                  {{
                    openingLoginSession
                      ? "Login Window Open..."
                      : "Open F95 Login Window"
                  }}
                </button>
                <button
                  @click="resetSession"
                  :disabled="resettingSession || openingLoginSession"
                  class="settings-btn ui-action-btn disabled:opacity-50"
                >
                  <IconLogout2 class="ui-action-icon" />
                  {{ resettingSession ? "Resetting..." : "Reset Session/Cookies" }}
                </button>
              </div>
              <p v-if="sessionMessage" class="copyable-feedback text-xs text-green-400 mt-2">
                {{ sessionMessage }}
              </p>
              <p v-if="sessionError" class="copyable-feedback text-xs text-red-400 mt-2">
                {{ sessionError }}
              </p>
            </div>

            <div
              class="flex items-center justify-between mt-6 p-4 rounded-lg"
              style="
                background: var(--bg-raised);
                border: 1px solid var(--border);
              "
            >
              <div>
                <h4
                  class="text-sm font-medium"
                  style="color: var(--text-primary)"
                >
                  Enable Debug Logging
                </h4>
                <p class="text-xs mt-1" style="color: var(--text-muted)">
                  Saves Wine/Proton launch output to a .log file next to the
                  game executable to help troubleshoot black screens.
                </p>
              </div>
              <label class="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  v-model="enableLogging"
                  class="sr-only peer"
                />
                <div class="ui-toggle"></div>
              </label>
            </div>

            <div
              class="motion-setting-card flex items-center justify-between mt-4 p-4 rounded-lg gap-4"
            >
              <div>
                <h4
                  class="text-sm font-medium"
                  style="color: var(--text-primary)"
                >
                  Animations
                </h4>
                <p class="text-xs mt-1" style="color: var(--text-muted)">
                  Turn non-essential UI motion on or off. Loading indicators stay visible either way.
                </p>
              </div>
              <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0">
                <button
                  type="button"
                  class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
                  :class="animationsEnabled ? 'motion-option-active' : ''"
                  @click="animationsEnabled = true"
                >
                  On
                </button>
                <button
                  type="button"
                  class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
                  :class="!animationsEnabled ? 'motion-option-active' : ''"
                  @click="animationsEnabled = false"
                >
                  Off
                </button>
              </div>
            </div>

            <hr style="border-color: var(--border)" class="my-4" />

            <div>
              <h4
                class="text-sm font-bold mb-2"
                style="color: var(--text-primary)"
              >
                Advanced Tools
              </h4>
              <p class="text-xs mb-4" style="color: var(--text-secondary)">
                Run these tools to fix common issues with certain game engines.
              </p>

              <div class="flex flex-col gap-3">
                <!-- DLL Install Card -->
                <div class="tool-card p-4 rounded-lg relative overflow-hidden">
                  <div
                    class="absolute inset-y-0 left-0 w-1"
                    :class="dllsInstalled ? 'bg-green-500' : ''"
                    :style="
                      !dllsInstalled ? 'background: var(--text-muted)' : ''
                    "
                  ></div>
                  <div class="flex items-center justify-between">
                    <div class="pl-3">
                      <h5
                        class="text-sm font-medium"
                        style="color: var(--text-primary)"
                      >
                        RPGMaker / Unity Fix (Winetricks)
                      </h5>
                      <p
                        v-if="!dllsInstalled && !installingDeps"
                        class="text-xs mt-1"
                        style="color: var(--text-muted)"
                      >
                        Installs corefonts, d3d, quartz, wmp9, directshow. Fixes
                        video decoding black screens.
                      </p>
                      <p
                        v-if="installingDeps"
                        class="text-xs text-yellow-400 mt-1 font-mono"
                      >
                        Installing {{ depsProgress.current }} ({{
                          depsProgress.done
                        }}/{{ depsProgress.total }})... This may take 30+
                        minutes.
                      </p>
                      <p
                        v-if="dllsInstalled && !installingDeps"
                        class="text-xs text-green-400 mt-1 font-mono"
                      >
                        Installed
                      </p>
                    </div>
                      <button
                        @click="installDeps"
                        :disabled="installingDeps || dllsInstalled"
                        class="settings-btn ui-action-btn shrink-0 disabled:opacity-50"
                      >
                        <IconLoader2
                          v-if="installingDeps"
                          class="ui-action-icon animate-spin"
                        />
                      {{
                        dllsInstalled
                          ? "Installed"
                          : installingDeps
                            ? "Installing..."
                            : "Install DLLs"
                      }}
                    </button>
                  </div>
                  <div
                    v-if="installingDeps && depsProgress.total > 0"
                    class="mt-3 ml-3"
                  >
                    <div
                      class="w-full rounded-full h-1.5 overflow-hidden"
                      style="background: var(--bg-raised)"
                    >
                      <div
                        class="h-1.5 rounded-full transition-all duration-500"
                        style="background: var(--brand)"
                        :style="{
                          width:
                            (depsProgress.done / depsProgress.total) * 100 +
                            '%',
                        }"
                      ></div>
                    </div>
                  </div>
                </div>

                <!-- RTP Install Card -->
                <div class="tool-card p-4 rounded-lg relative overflow-hidden">
                  <div
                    class="absolute inset-y-0 left-0 w-1"
                    :class="rtpsInstalled ? 'bg-green-500' : ''"
                    :style="
                      !rtpsInstalled ? 'background: var(--text-muted)' : ''
                    "
                  ></div>
                  <div class="flex items-center justify-between">
                    <div class="pl-3">
                      <h5
                        class="text-sm font-medium"
                        style="color: var(--text-primary)"
                      >
                        RPGMaker RTP (VX Ace, VX, XP, 2003)
                      </h5>
                      <p
                        v-if="!rtpsInstalled && !installingRtps"
                        class="text-xs mt-1"
                        style="color: var(--text-muted)"
                      >
                        Downloads and verifies the official RTP packages for VX
                        Ace, VX, XP, and 2003 in the default prefix shown above.
                      </p>
                      <p
                        v-if="installingRtps"
                        class="text-xs text-yellow-400 mt-1 font-mono"
                      >
                        Installing {{ rtpProgress.current }} ({{
                          rtpProgress.done
                        }}/{{ rtpProgress.total }})...
                      </p>
                      <p
                        v-if="rtpsInstalled && !installingRtps"
                        class="text-xs text-green-400 mt-1 font-mono"
                      >
                        Installed
                      </p>
                    </div>
                      <button
                        @click="installRtps"
                        :disabled="installingRtps || rtpsInstalled"
                        class="settings-btn ui-action-btn shrink-0 disabled:opacity-50"
                      >
                        <IconLoader2
                          v-if="installingRtps"
                          class="ui-action-icon animate-spin"
                        />
                      {{
                        rtpsInstalled
                          ? "Installed"
                          : installingRtps
                            ? "Processing..."
                            : "Install RTPs"
                      }}
                    </button>
                  </div>
                  <div
                    v-if="installingRtps && rtpProgress.total > 0"
                    class="mt-3 ml-3"
                  >
                    <div
                      class="w-full rounded-full h-1.5 overflow-hidden"
                      style="background: var(--bg-raised)"
                    >
                      <div
                        class="h-1.5 rounded-full transition-all duration-500"
                        style="background: var(--brand)"
                        :style="{
                          width:
                            (rtpProgress.done / rtpProgress.total) * 100 + '%',
                        }"
                      ></div>
                    </div>
                  </div>
                </div>

                <!-- Cheat Engine Card -->
                <div class="tool-card p-4 rounded-lg relative overflow-hidden">
                  <div
                    class="absolute inset-y-0 left-0 w-1"
                    :class="ceInstalled ? 'bg-green-500' : ''"
                    :style="!ceInstalled ? 'background: var(--text-muted)' : ''"
                  ></div>
                  <div class="pl-3">
                    <h5
                      class="text-sm font-medium"
                      style="color: var(--text-primary)"
                    >
                      Cheat Engine (Lunar Engine)
                    </h5>
                    <p
                      class="text-xs mt-1"
                      style="color: var(--text-muted)"
                      v-if="!ceInstalled"
                    >
                      Provides the ability to auto-inject CE when launching
                      games to modify values.
                    </p>
                    <p class="text-xs text-green-400 mt-1 font-mono" v-else>
                      Installed
                    </p>
                  </div>
                  <button
                    @click="downloadCe"
                    :disabled="installingCe || ceInstalled"
                    class="settings-btn ui-action-btn absolute right-4 top-1/2 -translate-y-1/2 disabled:opacity-50"
                  >
                    <IconLoader2
                      v-if="installingCe"
                      class="ui-action-icon animate-spin"
                    />
                    {{
                      ceInstalled
                        ? "Installed"
                        : installingCe
                          ? "Downloading..."
                          : "Install CE"
                    }}
                  </button>
                </div>
              </div>

              <div class="mt-2 space-y-1">
                <p v-if="installError" class="copyable-feedback text-xs text-red-400">
                  {{ installError }}
                </p>
                <p v-if="rtpError" class="copyable-feedback text-xs text-red-400">
                  {{ rtpError }}
                </p>
                <p v-if="ceError" class="copyable-feedback text-xs text-red-400">{{ ceError }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div
        class="px-8 py-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="min-h-[1.25rem]">
          <p v-if="saveError" class="copyable-feedback text-xs text-red-400">
            {{ saveError }}
          </p>
          <p v-else-if="saveMessage" class="copyable-feedback text-xs text-green-400">
            {{ saveMessage }}
          </p>
        </div>
        <button
          @click="saveSettings"
          :disabled="saving"
          class="ui-action-btn text-white px-6 py-2 rounded-lg text-sm font-medium settings-save-btn disabled:opacity-50 disabled:cursor-wait"
          style="background: var(--brand); box-shadow: var(--shadow-brand)"
        >
          <IconDeviceFloppyFilled class="ui-action-icon" />
          {{ saving ? "Saving..." : "Save Changes" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.settings-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.625rem 1rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.settings-input::placeholder {
  color: var(--text-muted);
}
.settings-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.settings-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.settings-btn:hover {
  background: var(--border-hover);
}

.motion-setting-card,
.motion-toggle {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}

.motion-option {
  color: var(--text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}

.motion-option-active {
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-card);
}

.settings-save-btn {
  transition: filter 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
}

.tool-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
</style>
