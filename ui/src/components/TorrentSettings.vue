<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, onWebviewReady, type SettingsPayload, type TorrentSeedMode } from "../services/api";
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { notifyError } from "../utils/toast";
import { useSaveStatus } from "../composables/useSaveStatus";
import FieldSaveFeedback from "./FieldSaveFeedback.vue";
import SettingRow from "./settings/SettingRow.vue";
import SettingsGroup from "./settings/SettingsGroup.vue";
import { limitInMegabytes } from "../utils/downloadQueue";
import { parseTorrentField, SEED_MODE_OPTIONS, torrentFieldKey, torrentFieldText, type TorrentField } from "../utils/torrentSettings";

const settingsRowVisible = useSettingsRowVisibility();

const loaded = ref(false);
const seedMode = ref<TorrentSeedMode>("ask");
const portMapping = ref(false);
const watchFolder = ref("");
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

// One save status per row: "Saving…", "Saved" or the error shows on that row.
const status = {
  mode: useSaveStatus(),
  limits: useSaveStatus(),
  watch: useSaveStatus(),
  upload: useSaveStatus(),
  ports: useSaveStatus(),
};

const save = (rowStatus: ReturnType<typeof useSaveStatus>, payload: SettingsPayload): Promise<boolean> => rowStatus.run(async () => {
  const result = await api.saveSettings(payload);
  if (!result.success) throw new Error(result.error || "Unknown error");
});

const saveMode = async (event: Event) => {
  const value = (event.target as HTMLSelectElement).value as TorrentSeedMode;
  const previous = seedMode.value;
  seedMode.value = value;
  if (!(await save(status.mode, { torrent_seed_mode: value }))) seedMode.value = previous;
};

const savePortMapping = async (event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const previous = portMapping.value;
  portMapping.value = value;
  if (!(await save(status.ports, { torrent_port_mapping: value }))) portMapping.value = previous;
};

const saveWatchFolder = async (value: string) => {
  if (await save(status.watch, { torrent_watch_folder: value })) watchFolder.value = value.trim();
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
  const failed = !(await save(field === "upload" ? status.upload : status.limits, { [torrentFieldKey(field)]: parsed.value } as SettingsPayload));
  if (failed) await load();
};

onMounted(() => onWebviewReady(() => { void load(); }));
</script>

<template>
  <SettingsGroup id="torrents" title="Torrents">

    <SettingRow data-settings-row="torrents-seed-mode" data-keywords="torrent seed seeding ask always never" data-settings-bound v-show="settingsRowVisible('torrents-seed-mode')"
      label="After a torrent finishes" control-id="torrent-seed-mode" help="Seeding shares the files with others. It runs only while wLib is open."
      :save-state="status.mode.state.value" :save-error="status.mode.error.value">
      <select id="torrent-seed-mode" class="settings-input" :value="seedMode" :disabled="!loaded" @change="saveMode">
        <option v-for="option in SEED_MODE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </SettingRow>

    <SettingRow data-settings-row="torrents-seed-limits" data-keywords="torrent seed ratio hours time limit" data-settings-bound v-show="settingsRowVisible('torrents-seed-limits')"
      layout="stacked" label="Seeding limits" help="Seeding stops at the first limit reached. Leave a field blank for no limit."
      :save-state="status.limits.state.value" :save-error="status.limits.error.value">
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="torrent-seed-ratio" class="settings-field-label">Stop at ratio</label>
          <input id="torrent-seed-ratio" v-model="text.ratio" inputmode="decimal" class="settings-input w-full mt-1" placeholder="1.0" :disabled="!loaded" :aria-invalid="!!errors.ratio" @change="commit('ratio')" />
          <p v-if="errors.ratio" class="save-status-error mt-1" role="alert">{{ errors.ratio }}</p>
        </div>
        <div>
          <label for="torrent-seed-hours" class="settings-field-label">Stop after (hours)</label>
          <input id="torrent-seed-hours" v-model="text.hours" inputmode="decimal" class="settings-input w-full mt-1" placeholder="No limit" :disabled="!loaded" :aria-invalid="!!errors.hours" @change="commit('hours')" />
          <p v-if="errors.hours" class="save-status-error mt-1" role="alert">{{ errors.hours }}</p>
        </div>
      </div>
    </SettingRow>

    <SettingRow data-settings-row="torrents-watch-folder" data-keywords="torrent watch folder external client qbittorrent transmission deluge send magnet" data-settings-bound v-show="settingsRowVisible('torrents-watch-folder')"
      layout="stacked" label="Watch folder" control-id="torrent-watch-folder"
      help="When set, Download in the update dialog becomes Send to client. wLib puts the .torrent file in this folder, and your torrent client downloads it. This device only. Leave blank to download in wLib."
      :save-state="status.watch.state.value" :save-error="status.watch.error.value">
      <div class="flex flex-wrap gap-2">
        <input id="torrent-watch-folder" :value="watchFolder" class="settings-input flex-1 min-w-0" placeholder="No watch folder" spellcheck="false" :disabled="!loaded" :aria-invalid="status.watch.state.value === 'error'" aria-describedby="torrent-watch-folder-warning" @change="commitWatchFolder" />
        <button class="ui-card-btn" :disabled="!loaded" @click="browseWatchFolder">Browse</button>
        <button v-if="watchFolder" class="ui-card-btn" :disabled="!loaded" @click="saveWatchFolder('')">Clear</button>
      </div>
      <template #after>
        <p id="torrent-watch-folder-warning" class="text-xs" style="color:var(--warning-text)">.torrent files and magnet links contain your private RPDL passkey. Do not share them.</p>
      </template>
    </SettingRow>

    <SettingRow data-settings-row="torrents-network" data-keywords="torrent speed limit upload port upnp nat-pmp router" data-settings-bound v-show="settingsRowVisible('torrents-network')"
      layout="stacked" label="Network" help="The upload limit and port mapping apply to this device only. 0 or blank means unlimited. The download speed limit is under In-app downloads, for file hosts and torrents together.">
      <div class="space-y-3">
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <div class="flex items-center justify-between gap-2">
              <label for="torrent-upload-limit" class="settings-field-label">Upload limit (KiB/s)</label>
              <FieldSaveFeedback v-if="status.upload.state.value !== 'error'" :state="status.upload.state.value" :error="status.upload.error.value" />
            </div>
            <input id="torrent-upload-limit" v-model="text.upload" inputmode="numeric" class="settings-input w-full mt-1" placeholder="Unlimited" :disabled="!loaded" :aria-invalid="!!errors.upload" @change="commit('upload')" />
            <p v-if="errors.upload" class="save-status-error mt-1" role="alert">{{ errors.upload }}</p>
            <p v-else class="text-xs mt-1" style="color:var(--text-muted)" data-testid="torrent-upload-mb">{{ limitInMegabytes(text.upload.trim() || 0) }}. 1024 KiB/s is 1 MB/s.</p>
            <FieldSaveFeedback v-if="status.upload.state.value === 'error'" class="mt-1" :state="status.upload.state.value" :error="status.upload.error.value" />
          </div>
        </div>
        <div class="space-y-1">
          <label class="flex items-center justify-between gap-3 cursor-pointer">
            <span>
              <span class="text-sm font-medium block" style="color:var(--text-primary)">Ask my router to open a port</span>
              <span class="text-xs block mt-1" style="color:var(--text-muted)">Lets other people's torrent apps connect to you, which can make torrents faster. Uses UPnP or NAT-PMP, which most home routers support. Off by default.</span>
            </span>
            <span class="flex items-center gap-3 shrink-0">
              <FieldSaveFeedback v-if="status.ports.state.value !== 'error'" class="save-status-slot" :state="status.ports.state.value" :error="status.ports.error.value" />
              <span class="relative inline-flex">
                <input type="checkbox" role="switch" :checked="portMapping" :disabled="!loaded" class="sr-only peer" aria-label="Ask my router to open a port" @change="savePortMapping" />
                <span class="ui-toggle"></span>
              </span>
            </span>
          </label>
          <FieldSaveFeedback v-if="status.ports.state.value === 'error'" :state="status.ports.state.value" :error="status.ports.error.value" />
        </div>
      </div>
    </SettingRow>
  </SettingsGroup>
</template>
