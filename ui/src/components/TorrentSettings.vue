<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { IconMagnet } from "@tabler/icons-vue";
import { api, onWebviewReady, type SettingsPayload, type TorrentSeedMode } from "../services/api";
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { notifyError } from "../utils/toast";
import { parseTorrentField, SEED_MODE_OPTIONS, torrentFieldKey, torrentFieldText, type TorrentField } from "../utils/torrentSettings";

const settingsRowVisible = useSettingsRowVisibility();

const loaded = ref(false);
const seedMode = ref<TorrentSeedMode>("ask");
const portMapping = ref(false);
const watchFolder = ref("");
const watchError = ref("");
const text = ref<Record<TorrentField, string>>({ ratio: "1", hours: "", upload: "" });
const errors = ref<Record<TorrentField, string>>({ ratio: "", hours: "", upload: "" });

const load = async () => {
  try {
    const data = await api.getSettings();
    seedMode.value = data.torrent_seed_mode;
    portMapping.value = data.torrent_port_mapping;
    watchFolder.value = data.torrent_watch_folder || "";
    text.value = {
      ratio: torrentFieldText(data.torrent_seed_ratio),
      hours: torrentFieldText(data.torrent_seed_hours),
      upload: torrentFieldText(data.torrent_upload_limit_kib),
    };
    loaded.value = true;
  } catch (error) {
    console.error("Could not load torrent settings", error);
  }
};

const save = async (payload: SettingsPayload, failure: string): Promise<boolean> => {
  try {
    const result = await api.saveSettings(payload);
    if (!result.success) throw new Error(result.error || "Unknown error");
    return true;
  } catch (error) {
    notifyError(`${failure}: ${error instanceof Error ? error.message : String(error)}`);
    return false;
  }
};

const saveMode = async (event: Event) => {
  const value = (event.target as HTMLSelectElement).value as TorrentSeedMode;
  const previous = seedMode.value;
  seedMode.value = value;
  if (!(await save({ torrent_seed_mode: value }, "Could not save the seeding choice"))) seedMode.value = previous;
};

const savePortMapping = async (event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const previous = portMapping.value;
  portMapping.value = value;
  if (!(await save({ torrent_port_mapping: value }, "Could not save port mapping"))) portMapping.value = previous;
};

const saveWatchFolder = async (value: string) => {
  try {
    const result = await api.saveSettings({ torrent_watch_folder: value });
    if (!result.success) {
      watchError.value = result.error || "Could not save the watch folder";
      return;
    }
    watchError.value = "";
    watchFolder.value = value.trim();
  } catch (error) {
    watchError.value = error instanceof Error ? error.message : String(error);
  }
};

const browseWatchFolder = async () => {
  try {
    const picked = await api.browseDirectory(watchFolder.value);
    if (picked) await saveWatchFolder(picked);
  } catch (error) {
    notifyError(`Could not open the folder picker: ${error instanceof Error ? error.message : String(error)}`);
  }
};

const commitWatchFolder = (event: Event) => { void saveWatchFolder((event.target as HTMLInputElement).value); };

const commit = async (field: TorrentField) => {
  const parsed = parseTorrentField(field, text.value[field]);
  errors.value[field] = parsed.error;
  if (parsed.value === null) return;
  const failed = !(await save({ [torrentFieldKey(field)]: parsed.value } as SettingsPayload, "Could not save the torrent setting"));
  if (failed) await load();
};

onMounted(() => onWebviewReady(() => { void load(); }));
</script>

<template>
  <section id="torrents" class="settings-card rounded-xl p-6 space-y-5" aria-labelledby="torrents-settings-heading">
    <h3 id="torrents-settings-heading" class="ui-section-heading text-lg font-semibold" style="color:var(--text-primary)">
      <IconMagnet class="ui-section-icon" />
      Torrents
    </h3>

    <div data-settings-row="torrents-seed-mode" data-keywords="torrent seed seeding ask always never" data-settings-bound v-show="settingsRowVisible('torrents-seed-mode')" class="ui-card-row p-4 rounded-lg space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <label for="torrent-seed-mode" class="text-sm font-medium" style="color:var(--text-primary)">After a torrent finishes</label>
          <p class="text-xs mt-1" style="color:var(--text-muted)">Seeding shares the files with others. It runs only while wLib is open.</p>
        </div>
        <select id="torrent-seed-mode" class="torrent-input" :value="seedMode" :disabled="!loaded" @change="saveMode">
          <option v-for="option in SEED_MODE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
    </div>

    <div data-settings-row="torrents-seed-limits" data-keywords="torrent seed ratio hours time limit" data-settings-bound v-show="settingsRowVisible('torrents-seed-limits')" class="ui-card-row p-4 rounded-lg space-y-3">
      <p class="text-xs" style="color:var(--text-muted)">Seeding stops at the first limit reached. Leave a field blank for no limit.</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="torrent-seed-ratio" class="text-sm font-medium" style="color:var(--text-primary)">Stop at ratio</label>
          <input id="torrent-seed-ratio" v-model="text.ratio" inputmode="decimal" class="torrent-input w-full mt-1" placeholder="1.0" :disabled="!loaded" :aria-invalid="!!errors.ratio" @change="commit('ratio')" />
          <p v-if="errors.ratio" class="text-xs mt-1" style="color:var(--danger-text)" role="alert">{{ errors.ratio }}</p>
        </div>
        <div>
          <label for="torrent-seed-hours" class="text-sm font-medium" style="color:var(--text-primary)">Stop after (hours)</label>
          <input id="torrent-seed-hours" v-model="text.hours" inputmode="decimal" class="torrent-input w-full mt-1" placeholder="No limit" :disabled="!loaded" :aria-invalid="!!errors.hours" @change="commit('hours')" />
          <p v-if="errors.hours" class="text-xs mt-1" style="color:var(--danger-text)" role="alert">{{ errors.hours }}</p>
        </div>
      </div>
    </div>

    <div data-settings-row="torrents-watch-folder" data-keywords="torrent watch folder external client qbittorrent transmission deluge send magnet" data-settings-bound v-show="settingsRowVisible('torrents-watch-folder')" class="ui-card-row p-4 rounded-lg space-y-3">
      <div>
        <label for="torrent-watch-folder" class="text-sm font-medium" style="color:var(--text-primary)">Watch folder</label>
        <p class="text-xs mt-1" style="color:var(--text-muted)">When set, Download in the update dialog becomes Send to client. wLib puts the .torrent file in this folder, and your torrent client downloads it. This device only. Leave blank to download in wLib.</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <input id="torrent-watch-folder" :value="watchFolder" class="torrent-input flex-1 min-w-0" placeholder="No watch folder" spellcheck="false" :disabled="!loaded" :aria-invalid="!!watchError" aria-describedby="torrent-watch-folder-warning" @change="commitWatchFolder" />
        <button class="ui-card-btn" :disabled="!loaded" @click="browseWatchFolder">Browse</button>
        <button v-if="watchFolder" class="ui-card-btn" :disabled="!loaded" @click="saveWatchFolder('')">Clear</button>
      </div>
      <p v-if="watchError" class="text-xs" style="color:var(--danger-text)" role="alert">{{ watchError }}</p>
      <p id="torrent-watch-folder-warning" class="text-xs" style="color:var(--warning-text)">.torrent files and magnet links contain your private RPDL passkey. Do not share them.</p>
    </div>

    <div data-settings-row="torrents-network" data-keywords="torrent speed limit upload port upnp nat-pmp router" data-settings-bound v-show="settingsRowVisible('torrents-network')" class="ui-card-row p-4 rounded-lg space-y-3">
      <p class="text-xs" style="color:var(--text-muted)">The upload limit and port mapping apply to this device only. 0 or blank means unlimited. The download limit is in Library &amp; Downloads, for file hosts and torrents together.</p>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="torrent-upload-limit" class="text-sm font-medium" style="color:var(--text-primary)">Upload limit (KiB/s)</label>
          <input id="torrent-upload-limit" v-model="text.upload" inputmode="numeric" class="torrent-input w-full mt-1" placeholder="Unlimited" :disabled="!loaded" :aria-invalid="!!errors.upload" @change="commit('upload')" />
          <p v-if="errors.upload" class="text-xs mt-1" style="color:var(--danger-text)" role="alert">{{ errors.upload }}</p>
        </div>
      </div>
      <label class="flex items-center justify-between gap-3 cursor-pointer">
        <span>
          <span class="text-sm font-medium block" style="color:var(--text-primary)">Ask my router to open a port</span>
          <span class="text-xs block mt-1" style="color:var(--text-muted)">Uses UPnP or NAT-PMP so more peers can reach you. Off by default.</span>
        </span>
        <span class="relative inline-flex">
          <input type="checkbox" :checked="portMapping" :disabled="!loaded" class="sr-only peer" aria-label="Ask my router to open a port" @change="savePortMapping" />
          <span class="ui-toggle"></span>
        </span>
      </label>
    </div>
  </section>
</template>

<style scoped>
.torrent-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 0.875rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.torrent-input::placeholder { color: var(--text-muted); }
.torrent-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}
.torrent-input:disabled { opacity: 0.6; }
</style>
