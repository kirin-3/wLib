<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch, type Component, type Ref } from "vue";
import {
  IconAlertTriangle,
  IconBookmark,
  IconDatabaseExport,
  IconEye,
  IconFileExport,
  IconFileImport,
  IconFolder,
  IconFolderCog,
  IconFolderOpen,
  IconInfoCircle,
  IconListDetails,
  IconLoader2,
  IconLock,
  IconSettings,
  IconTerminal2,
  IconUserCheck,
  IconWorldDownload,
  IconX,
} from "@tabler/icons-vue";
import { api } from "../services/api";
import ExternalImportPanel from "../components/ExternalImportPanel.vue";
import PathStatus from "../components/PathStatus.vue";
import {
  F95AccountImport, createF95AccountPolling, f95AccountPreviewNote, f95AccountProgressText, f95AccountResultText,
} from "../utils/f95AccountImport";
import type {
  LibraryBackupInspectResponse,
  LibraryBackupSection,
  LibraryBackupWarning,
} from "../services/api";
import { useRoute } from "vue-router";

type BackupTab = "export" | "import" | "external" | "f95";

interface SectionOption {
  id: LibraryBackupSection;
  label: string;
  description: string;
  icon: Component;
}

const sectionOptions: SectionOption[] = [
  {
    id: "user_state",
    label: "Statuses, ratings, notes, and playtime",
    description: "Includes play status, favorites, ratings, progress notes, and play history.",
    icon: IconUserCheck,
  },
  {
    id: "launch_config",
    label: "Launch options",
    description: "Includes launch mode, arguments, locale flags, Wine/Proton overrides, and CE injection.",
    icon: IconTerminal2,
  },
  {
    id: "executable_paths",
    label: "Game executable paths",
    description: "Useful for migration when games live in the same or similar folder layout.",
    icon: IconFolder,
  },
  {
    id: "launch_targets",
    label: "Extra launch targets",
    description: "Includes named additional executables for multi-part games.",
    icon: IconListDetails,
  },
  {
    id: "settings_general",
    label: "General wLib settings",
    description: "Includes portable app preferences such as logging and update-check cadence.",
    icon: IconSettings,
  },
  {
    id: "settings_paths",
    label: "Machine-specific settings paths",
    description: "Includes Proton, Wine prefix, and RPGMaker runner paths.",
    icon: IconFolderCog,
  },
];
const metadataSectionOption: SectionOption = {
  id: "metadata",
  label: "Game metadata",
  description:
    "Updates title, developer, engine, tags, F95 URL, versions, and cover reference.",
  icon: IconDatabaseExport,
};
const coversSectionOption: SectionOption = {
  id: "covers", label: "Covers", description: "Copies local cover images onto this device.", icon: IconFolder,
};
const importSectionOptions: SectionOption[] = [metadataSectionOption, ...sectionOptions, coversSectionOption];

const defaultExportSections: LibraryBackupSection[] = [
  "user_state",
  "launch_config",
  "executable_paths",
  "launch_targets",
  "settings_general",
];

const activeTab = ref<BackupTab>("export");
const exportPath = ref("");
const importPath = ref("");
const exportSections = ref<LibraryBackupSection[]>([...defaultExportSections]);
const importSections = ref<LibraryBackupSection[]>([]);
const inspectResult = ref<LibraryBackupInspectResponse | null>(null);
const exportMessage = ref("");
const exportError = ref("");
const importMessage = ref("");
const importError = ref("");
const exporting = ref(false);
const inspecting = ref(false);
const importing = ref(false);
const encryptExport = ref(false);
const exportPassphrase = ref("");
const exportConfirmation = ref("");
const importPassphrase = ref("");
const importNeedsPassphrase = ref(false);
// Bumped when the path changes so a late answer for the previous
// file cannot populate the new preview.
let importInspectRequest = 0;
watch(importPath, () => {
  importInspectRequest++;
  inspecting.value = false;
  importPassphrase.value = "";
  importNeedsPassphrase.value = false;
  inspectResult.value = null;
  importSections.value = [];
  importMessage.value = "";
  importError.value = "";
});
watch(encryptExport, () => { exportPassphrase.value = ""; exportConfirmation.value = ""; });

const importCounts = computed(() => inspectResult.value?.counts || null);
const availableImportSections = computed<LibraryBackupSection[]>(
  () => inspectResult.value?.available_sections || [],
);
const importWarnings = computed<LibraryBackupWarning[]>(
  () => inspectResult.value?.warnings || [],
);
const availableImportOptions = computed(() => {
  return importSectionOptions.filter((item) =>
    availableImportSections.value.includes(item.id),
  );
});
const canImportSelected = computed(() => {
  return Boolean(inspectResult.value) && importSections.value.length > 0;
});

const isSelected = (sections: LibraryBackupSection[], id: LibraryBackupSection) => {
  return sections.includes(id);
};

const toggleSectionValue = (
  target: Ref<LibraryBackupSection[]>,
  id: LibraryBackupSection,
) => {
  if (target.value.includes(id)) {
    target.value = target.value.filter((section) => section !== id);
  } else {
    target.value = [...target.value, id];
  }
};

const toggleExportSection = (id: LibraryBackupSection) => {
  toggleSectionValue(exportSections, id);
};

const toggleImportSection = (id: LibraryBackupSection) => {
  toggleSectionValue(importSections, id);
};

const browsing = ref(false);

const browseExportDestination = async () => {
  if (browsing.value) return;
  browsing.value = true;
  try {
    const selected = await api.browseDirectory(exportPath.value || "");
    if (selected) exportPath.value = selected;
  } catch (error) {
    exportError.value = `Could not open destination picker: ${String(error)}`;
  } finally {
    browsing.value = false;
  }
};

const browseImportFile = async () => {
  if (browsing.value) return;
  browsing.value = true;
  try {
    const selected = await api.browseBackupFile(importPath.value || "");
    if (selected) {
      importPath.value = selected;
      inspectResult.value = null;
      importSections.value = [];
      importMessage.value = "";
      importError.value = "";
    }
  } catch (error) {
    importError.value = `Could not open backup picker: ${String(error)}`;
  } finally {
    browsing.value = false;
  }
};

const runExport = async () => {
  if (exporting.value) return;
  exportMessage.value = "";
  exportError.value = "";
  if (encryptExport.value && (!exportPassphrase.value || exportPassphrase.value !== exportConfirmation.value)) {
    exportError.value = "Enter a passphrase and a matching confirmation.";
    return;
  }
  exporting.value = true;

  try {
    const result = await api.exportLibraryBackup(
      { sections: exportSections.value },
      exportPath.value,
      encryptExport.value ? exportPassphrase.value : null,
    );
    if (!result || result.success === false) {
      exportError.value = result?.error || "Export failed.";
      return;
    }
    exportMessage.value = `Exported ${result.game_count ?? 0} games to ${result.path || "the selected file"}.`;
    exportPassphrase.value = exportConfirmation.value = "";
  } catch (error) {
    exportError.value = `Export failed: ${String(error)}`;
  } finally {
    exporting.value = false;
  }
};

const inspectImport = async () => {
  if (inspecting.value) return;
  const path = importPath.value;
  const requestId = ++importInspectRequest;
  importMessage.value = "";
  importError.value = "";
  inspectResult.value = null;
  inspecting.value = true;

  try {
    const result = await api.inspectLibraryBackup(path, importPassphrase.value || null);
    if (requestId !== importInspectRequest || importPath.value !== path) return;
    if (!result || result.success === false) {
      importError.value = result?.error || "Could not inspect backup.";
      if (result?.error_code === "passphrase_required") importNeedsPassphrase.value = true;
      return;
    }
    inspectResult.value = result;
    importNeedsPassphrase.value = false;
    importSections.value = (result.available_sections || []).filter(
      (section) => section !== "settings_paths",
    );
  } catch (error) {
    if (requestId !== importInspectRequest || importPath.value !== path) return;
    importError.value = `Could not inspect backup: ${String(error)}`;
  } finally {
    if (requestId === importInspectRequest) inspecting.value = false;
  }
};

const openExternalTab = () => {
  activeTab.value = "external";
};

// A link such as /import-export#external opens that tab (the Library's
// first-run state links here).
const route = useRoute();
const followTabHash = () => {
  const tab = route.hash.slice(1);
  if (tab === "external") openExternalTab();
  else if (tab === "export" || tab === "import" || tab === "f95") activeTab.value = tab;
};
watch(() => route.hash, followTabHash, { immediate: true });

const f95Account = reactive(new F95AccountImport(api));
const F95_POLL_MS = 500;
const f95Polling = createF95AccountPolling(f95Account, F95_POLL_MS);
const readF95Lists = () => f95Polling.read();
// A read keeps running in the backend while the user is on another page; show it again.
onMounted(() => { void f95Polling.attach(); });
const importF95Lists = async () => {
  if (await f95Account.importLists()) window.dispatchEvent(new Event("wlib-refresh-library"));
};
const f95ProgressText = computed(() => f95AccountProgressText(f95Account.progress));
const f95PreviewNote = computed(() => f95AccountPreviewNote(f95Account.preview));
const f95ResultText = computed(() => (f95Account.result ? f95AccountResultText(f95Account.result) : ""));
onBeforeUnmount(f95Polling.dispose);

const runImport = async () => {
  if (importing.value || !inspectResult.value) return;
  importMessage.value = "";
  importError.value = "";
  if (importSections.value.length === 0) {
    importError.value = "Select at least one section to import.";
    return;
  }
  importing.value = true;

  try {
    const result = await api.importLibraryBackup(importPath.value, {
      sections: importSections.value,
    }, importPassphrase.value || null);
    if (!result || result.success === false) {
      importError.value = result?.error || "Import failed.";
      return;
    }
    importMessage.value = `Imported backup. Created ${result.created ?? 0}, updated ${result.updated ?? 0}, skipped ${result.skipped ?? 0}. Custom tools: ${result.companion_tools_updated ?? 0} imported, ${result.companion_tools_skipped ?? 0} skipped.`;
    window.dispatchEvent(new Event("wlib-refresh-library"));
    importPassphrase.value = "";
    importNeedsPassphrase.value = false;
    inspectResult.value = null;
  } catch (error) {
    importError.value = `Import failed: ${String(error)}`;
  } finally {
    importing.value = false;
  }
};
</script>

<template>
  <div class="allow-text-selection backup-page mx-auto w-full p-8 max-w-5xl pb-12">
    <header class="mb-8">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconDatabaseExport class="ui-page-heading-icon" />
        <span>Import / Export</span>
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Move your library and selected settings with one JSON file.
      </p>
    </header>

    <div class="backup-panel">
      <div class="backup-tabs" role="group" aria-label="Import and export options">
        <button
          type="button"
          :class="['backup-tab', activeTab === 'export' ? 'backup-tab-active' : '']"
          @click="activeTab = 'export'"
        >
          <IconFileExport />
          Export
        </button>
        <button
          type="button"
          :class="['backup-tab', activeTab === 'import' ? 'backup-tab-active' : '']"
          @click="activeTab = 'import'"
        >
          <IconFileImport />
          Import
        </button>
        <button
          type="button"
          :class="['backup-tab', activeTab === 'external' ? 'backup-tab-active' : '']"
          @click="openExternalTab"
        >
          <IconFileImport />
          From another app
        </button>
        <button
          type="button"
          :class="['backup-tab', activeTab === 'f95' ? 'backup-tab-active' : '']"
          @click="activeTab = 'f95'"
        >
          <IconWorldDownload />
          F95 account
        </button>
      </div>

      <section v-if="activeTab === 'export'" class="backup-body">
        <div class="backup-note">
          <IconInfoCircle />
          <span>
            Game metadata is always included: title, developer, engine, tags,
            F95 URL, versions, and cover reference.
          </span>
        </div>

        <div class="backup-field">
          <label>Destination folder or JSON file</label>
          <div class="backup-path-row">
            <input
              v-model="exportPath"
              type="text"
              placeholder="Choose a folder or enter /path/to/wlib-export.json"
              class="backup-input"
            />
            <button type="button" class="backup-secondary" @click="browseExportDestination">
              <IconFolderOpen />
              Browse
            </button>
          </div>
          <PathStatus :path="exportPath" kind="parent" accept-directory />
        </div>

        <div class="backup-section-list">
          <label
            v-for="option in sectionOptions"
            :key="option.id"
            class="backup-check-row"
          >
            <component :is="option.icon" class="backup-option-icon" />
            <span class="backup-option-copy">
              <strong>{{ option.label }}</strong>
              <em>{{ option.description }}</em>
            </span>
            <input
              type="checkbox"
              :checked="isSelected(exportSections, option.id)"
              @change="toggleExportSection(option.id)"
            />
          </label>
        </div>

        <label class="backup-check-row">
          <IconLock class="backup-option-icon" aria-hidden="true" />
          <span class="backup-option-copy">
            <strong id="encrypt-export-title">Encrypt with passphrase</strong>
            <em id="encrypt-export-desc">Protects the file; you'll need the passphrase to import it.</em>
          </span>
          <input
            v-model="encryptExport"
            type="checkbox"
            aria-labelledby="encrypt-export-title"
            aria-describedby="encrypt-export-desc"
          />
        </label>
        <div v-if="encryptExport" class="backup-field backup-passphrase-fields">
          <p>A forgotten passphrase cannot be recovered. Without it, the export cannot be read.</p>
          <label for="export-passphrase">Export passphrase</label><input id="export-passphrase" v-model="exportPassphrase" class="backup-input" type="password" autocomplete="new-password" />
          <label for="export-confirmation">Confirm export passphrase</label><input id="export-confirmation" v-model="exportConfirmation" class="backup-input" type="password" autocomplete="new-password" />
        </div>

        <p class="backup-disclaimer">
          <IconInfoCircle />
          <span>
            Browser sessions, cookies, downloaded runtimes, caches, extension
            copies, and logs are never included.
          </span>
        </p>
      </section>

      <section v-else-if="activeTab === 'import'" class="backup-body">
        <div class="backup-field">
          <label>Backup JSON file</label>
          <div class="backup-path-row">
            <input
              v-model="importPath"
              type="text"
              placeholder="/path/to/wlib-export.json"
              class="backup-input"
            />
            <button type="button" class="backup-secondary" @click="browseImportFile">
              <IconFolderOpen />
              Browse
            </button>
          </div>
          <PathStatus :path="importPath" kind="file" />
        </div>

        <button
          type="button"
          class="backup-secondary backup-inspect"
          :disabled="inspecting || !importPath"
          @click="inspectImport"
        >
          <IconLoader2 v-if="inspecting" class="backup-spinner" />
          <IconFileImport v-else />
          {{ inspecting ? "Inspecting..." : "Inspect Backup" }}
        </button>

        <div v-if="importNeedsPassphrase" class="backup-field backup-passphrase-fields">
          <label for="import-passphrase">Backup passphrase</label><input id="import-passphrase" v-model="importPassphrase" class="backup-input" type="password" autocomplete="current-password" @keydown.enter="inspectImport" />
          <p>Enter the passphrase, then inspect the backup again.</p>
        </div>

        <div v-if="inspectResult" class="backup-preview">
          <div class="backup-counts">
            <span>Total: {{ importCounts?.total_games ?? 0 }}</span>
            <span>Matched: {{ importCounts?.matched_games ?? 0 }}</span>
            <span>New: {{ importCounts?.new_games ?? 0 }}</span>
            <span>Ambiguous: {{ importCounts?.ambiguous_games ?? 0 }}</span>
            <span v-if="importCounts?.companion_tools">Custom tools: {{ importCounts.companion_tools }}</span>
          </div>

          <div class="backup-note">
            <IconInfoCircle />
            <span>
              Selected fields use backup-wins merge behavior. Local values are
              kept only for sections you leave unchecked.
            </span>
          </div>

          <div class="backup-section-list compact">
            <label
              v-for="option in availableImportOptions"
              :key="option.id"
              class="backup-check-row"
            >
              <component :is="option.icon" class="backup-option-icon" />
              <span class="backup-option-copy">
                <strong>{{ option.label }}</strong>
                <em>{{ option.description }}</em>
              </span>
              <input
                type="checkbox"
                :checked="isSelected(importSections, option.id)"
                @change="toggleImportSection(option.id)"
              />
            </label>
          </div>

          <div class="backup-disclaimer">
            <IconInfoCircle />
            <span>
              Cover references are imported as links/paths only. If covers are
              stale or unavailable after migration, run update checks to refresh
              metadata.
            </span>
          </div>

          <div v-if="importWarnings.length" class="backup-warnings">
            <strong>
              <IconAlertTriangle />
              {{ importWarnings.length }} warning{{ importWarnings.length === 1 ? '' : 's' }}
            </strong>
            <ul>
              <li v-for="(warning, index) in importWarnings" :key="index">
                {{ warning.message }}
              </li>
            </ul>
          </div>
        </div>
      </section>

      <ExternalImportPanel v-else-if="activeTab === 'external'" variant="page" />

      <section v-else-if="activeTab === 'f95'" class="backup-body f95-account">
        <div class="backup-note">
          <IconInfoCircle />
          <span>
            wLib reads the Watched games and Bookmarked games lists of the F95Zone account
            that is signed in to the browser with the paired wLib extension. wLib never asks
            for your F95 password. New games are added to your wishlist. Every game gets the
            tag "Watched on F95" or "Bookmarked on F95". wLib changes nothing else on games
            already in your library, and never removes games or tags.
          </span>
        </div>

        <div class="f95-account-actions">
          <button
            type="button"
            class="backup-secondary"
            :disabled="f95Account.busy"
            @click="readF95Lists"
          >
            <IconLoader2 v-if="f95Account.phase === 'starting' || f95Account.phase === 'reading'" class="backup-spinner" />
            <IconWorldDownload v-else />
            Read my F95 lists
          </button>
          <button
            v-if="f95Account.phase === 'reading'"
            type="button"
            class="backup-secondary"
            :disabled="f95Account.cancelling"
            @click="f95Account.cancel()"
          >
            <IconX />
            {{ f95Account.cancelling ? "Cancelling..." : "Cancel" }}
          </button>
        </div>

        <div v-if="f95Account.phase === 'reading'" class="backup-note" role="status" aria-live="polite">
          <IconLoader2 class="backup-spinner" />
          <span>{{ f95ProgressText }}</span>
        </div>

        <div v-if="f95Account.phase === 'preview' && f95Account.preview" class="backup-preview">
          <p v-if="f95Account.emptyLists" class="f95-account-empty">
            No watched or bookmarked games were found on F95Zone. There is nothing to import.
          </p>
          <template v-else>
            <div class="f95-account-lists">
              <div class="f95-account-list">
                <strong><IconEye /> Watched games</strong>
                <span>{{ f95Account.preview.lists.watched.found }} found</span>
                <span>{{ f95Account.preview.lists.watched.new }} new</span>
                <span>{{ f95Account.preview.lists.watched.existing }} already in library</span>
              </div>
              <div class="f95-account-list">
                <strong><IconBookmark /> Bookmarked games</strong>
                <span>{{ f95Account.preview.lists.bookmarks.found }} found</span>
                <span>{{ f95Account.preview.lists.bookmarks.new }} new</span>
                <span>{{ f95Account.preview.lists.bookmarks.existing }} already in library</span>
              </div>
            </div>
            <div class="backup-counts">
              <span>Total: {{ f95Account.preview.total }}</span>
              <span>New: {{ f95Account.preview.new }}</span>
              <span>Already in library: {{ f95Account.preview.existing }}</span>
            </div>
            <p v-if="f95PreviewNote" class="f95-account-note">{{ f95PreviewNote }}</p>
          </template>
        </div>
      </section>

      <footer v-if="activeTab !== 'external'" class="backup-footer">
        <div class="backup-feedback">
          <p v-if="activeTab === 'export' && exportError" class="backup-error">
            {{ exportError }}
          </p>
          <p v-else-if="activeTab === 'export' && exportMessage" class="backup-success">
            {{ exportMessage }}
          </p>
          <p v-else-if="activeTab === 'import' && importError" class="backup-error">
            {{ importError }}
          </p>
          <p v-else-if="activeTab === 'import' && importMessage" class="backup-success">
            {{ importMessage }}
          </p>
          <p v-else-if="activeTab === 'f95' && f95Account.error" class="backup-error">
            {{ f95Account.error }}
          </p>
          <div v-else-if="activeTab === 'f95' && f95Account.result" class="backup-success">
            <p>Imported from F95: {{ f95ResultText }}.</p>
            <router-link v-if="f95Account.result.metadata_check === 'started'" to="/updates">
              View progress
            </router-link>
            <p v-else-if="f95Account.result.metadata_check === 'busy'">
              An update check is already running; run one later from Updates.
            </p>
          </div>
        </div>

        <div class="backup-actions">
          <button
            v-if="activeTab === 'export'"
            type="button"
            class="backup-primary"
            :disabled="exporting || !exportPath"
            @click="runExport"
          >
            <IconLoader2 v-if="exporting" class="backup-spinner" />
            <IconFileExport v-else />
            {{ exporting ? "Exporting..." : "Create Export" }}
          </button>
          <button
            v-else-if="activeTab === 'import'"
            type="button"
            class="backup-primary"
            :disabled="importing || !canImportSelected"
            @click="runImport"
          >
            <IconLoader2 v-if="importing" class="backup-spinner" />
            <IconFileImport v-else />
            {{ importing ? "Importing..." : "Import Selected" }}
          </button>
          <button
            v-else-if="activeTab === 'f95' && (f95Account.canImport || f95Account.phase === 'importing')"
            type="button"
            class="backup-primary"
            :disabled="!f95Account.canImport"
            @click="importF95Lists"
          >
            <IconLoader2 v-if="f95Account.phase === 'importing'" class="backup-spinner" />
            <IconFileImport v-else />
            {{ f95Account.phase === "importing" ? "Importing..." : "Import" }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.f95-account-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.f95-account-lists { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 0.75rem; }
.f95-account-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.75rem;
  color: var(--text-secondary);
  font-size: 0.875rem;
}
.f95-account-list strong { display: inline-flex; align-items: center; gap: 0.4rem; color: var(--text-primary); }
.f95-account-list svg { width: 1rem; height: 1rem; }
.f95-account-empty,
.f95-account-note { color: var(--text-secondary); font-size: 0.875rem; }
.backup-passphrase-fields { display: grid; gap: .5rem; }
.backup-passphrase-fields p { font-size: .875rem; color: var(--text-secondary); }
.backup-page button:focus-visible, .backup-page input[type="checkbox"]:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.backup-page {
  color: var(--text-primary);
  min-width: 0;
  width: 100%;
}

@media (max-width: 640px) {
  .backup-page { padding: 1rem; }
  .backup-path-row { flex-wrap: wrap; }
  .backup-path-row input { flex-basis: 100%; }
}

.backup-panel {
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  box-shadow: var(--shadow-card);
  color: var(--text-primary);
}

.backup-footer {
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.backup-tabs {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1.25rem 0;
}

.backup-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 600;
}

.backup-tab svg,
.backup-primary svg,
.backup-secondary svg {
  width: 1rem;
  height: 1rem;
}

.backup-tab-active {
  color: var(--text-primary);
  border-color: var(--brand);
}

.backup-body {
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.backup-field label {
  display: block;
  margin-bottom: 0.4rem;
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-weight: 600;
}

.backup-path-row {
  display: flex;
  gap: 0.75rem;
}

.backup-input {
  flex: 1;
  min-width: 0;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.625rem 0.75rem;
  color: var(--text-primary);
  font-size: 0.875rem;
}

.backup-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.backup-note,
.backup-disclaimer,
.backup-warnings {
  display: flex;
  align-items: flex-start;
  gap: 0.55rem;
  border: 1px solid var(--border);
  border-radius: 0.625rem;
  background: var(--bg-raised);
  padding: 0.75rem;
  color: var(--text-secondary);
  font-size: 0.8125rem;
}

.backup-note svg,
.backup-disclaimer svg {
  width: 1rem;
  height: 1rem;
  margin-top: 0.05rem;
  color: var(--brand);
  flex: 0 0 auto;
}

.backup-section-list {
  display: grid;
  gap: 0.625rem;
}

.backup-section-list.compact {
  gap: 0.5rem;
}

.backup-check-row {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.625rem;
  background: var(--bg-inset);
}

.backup-check-row input {
  margin-left: auto;
  flex: 0 0 auto;
}

.backup-option-icon {
  width: 1.125rem;
  height: 1.125rem;
  color: var(--text-secondary);
  flex: 0 0 auto;
}

.backup-option-copy {
  min-width: 0;
}

.backup-option-copy strong,
.backup-option-copy em {
  display: block;
}

.backup-option-copy strong {
  font-size: 0.875rem;
  font-weight: 650;
}

.backup-option-copy em {
  margin-top: 0.2rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-style: normal;
}

.backup-inspect {
  width: fit-content;
}

.backup-segmented {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.backup-segmented .backup-check-row {
  flex: 1 1 12rem;
}

.backup-preview {
  display: grid;
  gap: 0.875rem;
}

.backup-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.backup-counts span {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.35rem 0.55rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
}

.backup-warnings ul {
  margin: 0.5rem 0 0;
  padding: 0 0.5rem 0 1.25rem;
  max-height: 16rem;
  overflow-y: auto;
  list-style: disc;
}

.backup-warnings li {
  padding: 0.3rem 0;
  line-height: 1.4;
  overflow-wrap: anywhere;
  border-bottom: 1px solid var(--border);
}

.backup-warnings li:last-child {
  border-bottom: 0;
}

.backup-warnings {
  display: block;
}

.backup-warnings strong {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--text-primary);
}

.backup-warnings strong svg {
  width: 1rem;
  height: 1rem;
  color: var(--status-on-hold-text);
}

.backup-footer {
  border-top: 1px solid var(--border);
  border-bottom: 0;
  background: var(--bg-inset);
  display: flex;
  gap: 1rem;
  align-items: center;
  justify-content: space-between;
}

.backup-feedback {
  min-height: 1.25rem;
  font-size: 0.8125rem;
}

.backup-error {
  color: var(--danger-text);
}

.backup-success {
  color: var(--success-text);
}

.backup-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

.backup-primary,
.backup-secondary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border-radius: 0.5rem;
  padding: 0.55rem 0.85rem;
  font-size: 0.8125rem;
  font-weight: 650;
}

.backup-primary {
  background: var(--brand-deep);
  color: white;
}

.backup-secondary {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
}

.backup-primary:disabled,
.backup-secondary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.backup-spinner {
  width: 1rem;
  height: 1rem;
  animation: backup-spin 1s linear infinite;
}

@keyframes backup-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
