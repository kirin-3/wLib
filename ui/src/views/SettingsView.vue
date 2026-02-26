<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { api, onWebviewReady } from "../services/api.js";

const protonPath = ref("");
const prefixPath = ref("");
const enableLogging = ref(false);
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
const depsProgress = ref({ done: 0, total: 0, current: "" });
const rtpProgress = ref({ done: 0, total: 0, current: "" });
const systemDeps = ref(null);
const copiedCommand = ref(false);
let pollTimer = null;

const loadSettings = async () => {
  try {
    const data = await api.getSettings();
    if (data) {
      protonPath.value = data.proton_path || "";
      prefixPath.value = data.wine_prefix_path || "";
      enableLogging.value = !!data.enable_logging;
    }

    const ceCheck = await api.isCheatEngineInstalled();
    ceInstalled.value = !!ceCheck?.installed;
    cePath.value = ceCheck?.path || "";

    await pollInstallStatus();

    const sysDeps = await api.getSystemDepsCommand();
    if (sysDeps) systemDeps.value = sysDeps;
  } catch (e) {
    console.error("Failed to load settings", e);
  }
};

const pollInstallStatus = async () => {
  try {
    const s = await api.getInstallStatus();
    if (s) {
      dllsInstalled.value = !!s.dlls_installed;
      rtpsInstalled.value = !!s.rtps_installed;
      if (s.deps) {
        depsProgress.value = s.deps;
        installingDeps.value = !!s.deps.running;
      }
      if (s.rtps) {
        rtpProgress.value = s.rtps;
        installingRtps.value = !!s.rtps.running;
      }
    }
  } catch (e) {}
};

const browseProton = async () => {
  try {
    const p = await api.browseFile();
    if (p && p.success === false) {
      alert("Failed to browse file: " + (p.error || "Unknown error"));
    } else if (p) {
      protonPath.value = p;
    }
  } catch (e) {
    console.error("Browse proton error", e);
    alert("Error browsing file: " + e.toString());
  }
};

const browsePrefix = async () => {
  try {
    const p = await api.browseDirectory();
    if (p && p.success === false) {
      alert("Failed to browse directory: " + (p.error || "Unknown error"));
    } else if (p) {
      prefixPath.value = p;
    }
  } catch (e) {
    console.error("Browse prefix error", e);
    alert("Error browsing directory: " + e.toString());
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
    protonError.value = e.toString();
  } finally {
    downloadingProton.value = false;
  }
};

const installRtps = async () => {
  installingRtps.value = true;
  rtpError.value = "";
  try {
    const result = await api.installRpgmakerRtp();
    if (result && result.success) {
      startPolling();
    } else {
      rtpError.value = result?.error || "Unknown error occurred";
      installingRtps.value = false;
    }
  } catch (e) {
    rtpError.value = e.toString();
    installingRtps.value = false;
  }
};

const installDeps = async () => {
  installingDeps.value = true;
  installError.value = "";
  try {
    const result = await api.installRpgmakerDependencies();
    if (result && result.success) {
      startPolling();
    } else {
      installError.value = result?.error || "Unknown error occurred";
      installingDeps.value = false;
    }
  } catch (e) {
    installError.value = e.toString();
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
      cePath.value = result.path;
    } else {
      ceError.value = result?.error || "Unknown error occurred";
    }
  } catch (e) {
    ceError.value = e.toString();
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
});

const saving = ref(false);
const saveSettings = async () => {
  saving.value = true;
  try {
    const res = await api.saveSettings({
      proton_path: protonPath.value,
      wine_prefix_path: prefixPath.value,
      enable_logging: enableLogging.value,
    });
    if (res && res.success === false) {
      alert("Failed to save settings: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error(e);
    alert("Error saving settings: " + e.toString());
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <div class="p-8 max-w-4xl pb-12">
    <header class="mb-10">
      <h2
        class="text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        Settings
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
            class="text-lg font-semibold mb-4 flex items-center gap-2"
            style="color: var(--text-primary)"
          >
            <svg
              class="w-5 h-5"
              style="color: var(--brand)"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"
              />
              <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
              <line x1="12" x2="12" y1="22.08" y2="12" />
            </svg>
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
                  class="text-xs font-medium flex items-center gap-1 disabled:opacity-50"
                  style="color: var(--brand)"
                >
                  <svg
                    v-if="downloadingProton"
                    class="w-3 h-3 animate-spin"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <circle cx="12" cy="12" r="10" />
                    <path d="M12 2a10 10 0 0 1 10 10" />
                  </svg>
                  <svg
                    v-else
                    class="w-3 h-3"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" x2="12" y1="15" y2="3" />
                  </svg>
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
                <button @click="browseProton" class="settings-btn">
                  Browse
                </button>
              </div>
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                Leave empty to use the system default `wine` command.
              </p>
              <p v-if="protonError" class="text-xs text-red-400 mt-1">
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
                <button @click="browsePrefix" class="settings-btn">
                  Browse
                </button>
              </div>
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                The location where game dependencies and save files will be
                isolated.
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
                <div
                  class="settings-toggle peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
                ></div>
              </label>
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
                      class="settings-btn flex items-center gap-2 shrink-0 disabled:opacity-50"
                    >
                      <svg
                        v-if="installingDeps"
                        class="w-3 h-3 animate-spin"
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                      >
                        <circle cx="12" cy="12" r="10" />
                        <path d="M12 2a10 10 0 0 1 10 10" />
                      </svg>
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
                        RPGMaker XP / VX Ace RTP
                      </h5>
                      <p
                        v-if="!rtpsInstalled && !installingRtps"
                        class="text-xs mt-1"
                        style="color: var(--text-muted)"
                      >
                        Downloads official RGSS-RTP missing files (rgss104e,
                        rgss3a) into your prefix.
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
                      class="settings-btn flex items-center gap-2 shrink-0 disabled:opacity-50"
                    >
                      <svg
                        v-if="installingRtps"
                        class="w-3 h-3 animate-spin"
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                      >
                        <circle cx="12" cy="12" r="10" />
                        <path d="M12 2a10 10 0 0 1 10 10" />
                      </svg>
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
                    class="settings-btn flex items-center gap-2 absolute right-4 top-1/2 -translate-y-1/2 disabled:opacity-50"
                  >
                    <svg
                      v-if="installingCe"
                      class="w-3 h-3 animate-spin"
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                    >
                      <circle cx="12" cy="12" r="10" />
                      <path d="M12 2a10 10 0 0 1 10 10" />
                    </svg>
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
                <p v-if="installError" class="text-xs text-red-400">
                  {{ installError }}
                </p>
                <p v-if="rtpError" class="text-xs text-red-400">
                  {{ rtpError }}
                </p>
                <p v-if="ceError" class="text-xs text-red-400">{{ ceError }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div
        class="px-8 py-5 flex justify-end gap-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <button
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style="color: var(--text-secondary)"
        >
          Reset
        </button>
        <button
          @click="saveSettings"
          class="text-white px-6 py-2 rounded-lg text-sm font-medium transition-all"
          style="background: var(--brand); box-shadow: var(--shadow-brand)"
        >
          Save Changes
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
  transition: all 0.15s ease;
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
  transition: all 0.15s ease;
}
.settings-btn:hover {
  background: var(--border-hover);
}

.settings-toggle {
  width: 2.75rem;
  height: 1.5rem;
  background: var(--bg-overlay);
  border-radius: 9999px;
}

.tool-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
</style>
