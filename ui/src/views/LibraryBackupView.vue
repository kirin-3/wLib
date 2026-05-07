<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, type Component, type Ref } from "vue";
import {
  IconAlertTriangle,
  IconDatabaseExport,
  IconFileExport,
  IconFileImport,
  IconFolder,
  IconFolderCog,
  IconFolderOpen,
  IconInfoCircle,
  IconListDetails,
  IconLoader2,
  IconSettings,
  IconTerminal2,
  IconUserCheck,
} from "@tabler/icons-vue";
import { api } from "../services/api";
import type {
  LibraryBackupInspectResponse,
  LibraryBackupSection,
  LibraryBackupWarning,
} from "../services/api";

type BackupTab = "export" | "import";

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
    description: "Includes Proton, Wine prefix, browser, and RPGMaker runner paths.",
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
const importSectionOptions: SectionOption[] = [metadataSectionOption, ...sectionOptions];

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

const browseExportDestination = async () => {
  try {
    const selected = await api.browseDirectory(exportPath.value || "");
    if (selected) exportPath.value = selected;
  } catch (error) {
    exportError.value = `Could not open destination picker: ${String(error)}`;
  }
};

const browseImportFile = async () => {
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
  }
};

const runExport = async () => {
  if (exporting.value) return;
  exportMessage.value = "";
  exportError.value = "";
  exporting.value = true;

  try {
    const result = await api.exportLibraryBackup(
      { sections: exportSections.value },
      exportPath.value,
    );
    if (!result || result.success === false) {
      exportError.value = result?.error || "Export failed.";
      return;
    }
    exportMessage.value = `Exported ${result.game_count ?? 0} games to ${result.path || "the selected file"}.`;
  } catch (error) {
    exportError.value = `Export failed: ${String(error)}`;
  } finally {
    exporting.value = false;
  }
};

const inspectImport = async () => {
  if (inspecting.value) return;
  importMessage.value = "";
  importError.value = "";
  inspectResult.value = null;
  inspecting.value = true;

  try {
    const result = await api.inspectLibraryBackup(importPath.value);
    if (!result || result.success === false) {
      importError.value = result?.error || "Could not inspect backup.";
      return;
    }
    inspectResult.value = result;
    importSections.value = (result.available_sections || []).filter(
      (section) => section !== "settings_paths",
    );
  } catch (error) {
    importError.value = `Could not inspect backup: ${String(error)}`;
  } finally {
    inspecting.value = false;
  }
};

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
    });
    if (!result || result.success === false) {
      importError.value = result?.error || "Import failed.";
      return;
    }
    importMessage.value = `Imported backup. Created ${result.created ?? 0}, updated ${result.updated ?? 0}, skipped ${result.skipped ?? 0}.`;
    window.dispatchEvent(new Event("wlib-refresh-library"));
  } catch (error) {
    importError.value = `Import failed: ${String(error)}`;
  } finally {
    importing.value = false;
  }
};
</script>

<template>
  <div class="allow-text-selection backup-page p-8 max-w-5xl pb-12">
    <header class="backup-page-header">
      <div class="backup-title-block">
        <IconDatabaseExport class="backup-title-icon" />
        <div>
          <h2 class="backup-page-title">Import / Export</h2>
          <p>Move your library and selected settings with one JSON file.</p>
        </div>
      </div>
    </header>

    <div class="backup-panel">
      <div class="backup-tabs" role="tablist">
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

        <p class="backup-disclaimer">
          <IconInfoCircle />
          <span>
            Browser sessions, cookies, downloaded runtimes, caches, extension
            copies, and logs are never included.
          </span>
        </p>
      </section>

      <section v-else class="backup-body">
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

        <div v-if="inspectResult" class="backup-preview">
          <div class="backup-counts">
            <span>Total: {{ importCounts?.total_games ?? 0 }}</span>
            <span>Matched: {{ importCounts?.matched_games ?? 0 }}</span>
            <span>New: {{ importCounts?.new_games ?? 0 }}</span>
            <span>Ambiguous: {{ importCounts?.ambiguous_games ?? 0 }}</span>
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
              <li v-for="(warning, index) in importWarnings.slice(0, 4)" :key="index">
                {{ warning.message }}
              </li>
            </ul>
          </div>
        </div>
      </section>

      <footer class="backup-footer">
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
            v-else
            type="button"
            class="backup-primary"
            :disabled="importing || !canImportSelected"
            @click="runImport"
          >
            <IconLoader2 v-if="importing" class="backup-spinner" />
            <IconFileImport v-else />
            {{ importing ? "Importing..." : "Import Selected" }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.backup-page {
  color: var(--text-primary);
}

.backup-page-header {
  margin-bottom: 1.5rem;
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

.backup-title-block {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

.backup-title-icon {
  width: 1.75rem;
  height: 1.75rem;
  margin-top: 0.15rem;
  color: var(--brand);
}

.backup-page-title {
  margin: 0;
  font-size: 1.875rem;
  line-height: 2.25rem;
  font-weight: 800;
  letter-spacing: -0.025em;
}

.backup-page-header p {
  margin: 0.25rem 0 0;
  color: var(--text-secondary);
  font-size: 0.875rem;
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
  padding-left: 1rem;
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
  color: #f87171;
}

.backup-success {
  color: #4ade80;
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
  background: var(--brand);
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
