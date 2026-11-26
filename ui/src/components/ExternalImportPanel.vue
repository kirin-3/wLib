<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, ref, watch, type Component } from "vue";
import {
  IconAlertTriangle,
  IconFileImport,
  IconFolder,
  IconFolderOpen,
  IconInfoCircle,
  IconListDetails,
  IconLoader2,
  IconUserCheck,
} from "@tabler/icons-vue";
import { api } from "../services/api";
import PathStatus from "./PathStatus.vue";
import type {
  ExternalLibraryImportResponse,
  ExternalLibraryInspectResponse,
  ExternalLibraryMergeMode,
  ExternalLibrarySource,
  LibraryBackupSection,
  LibraryBackupWarning,
} from "../services/api";

const props = withDefaults(
  defineProps<{
    variant?: "page" | "embedded";
  }>(),
  {
    variant: "page",
  },
);

const emit = defineEmits<{
  busy: [value: boolean];
  imported: [result: ExternalLibraryImportResponse];
}>();

interface ExternalSectionOption {
  id: LibraryBackupSection;
  label: string;
  description: string;
  icon: Component;
}

const externalSources: { id: ExternalLibrarySource; label: string }[] = [
  { id: "f95checker", label: "F95Checker" },
  { id: "atlas", label: "Atlas" },
  { id: "xlibrary", label: "XLibrary" },
  { id: "f69", label: "f69" },
  { id: "playnite", label: "Playnite" },
  { id: "gamelist", label: "Game-List" },
];

const externalImportSectionIds: LibraryBackupSection[] = [
  "covers",
  "user_state",
  "executable_paths",
  "launch_targets",
];

const externalSectionOptions: ExternalSectionOption[] = [
  {
    id: "user_state",
    label: "Statuses, ratings, notes, and playtime",
    description: "Includes play status, favorites, ratings, progress notes, and play history.",
    icon: IconUserCheck,
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
    id: "covers",
    label: "Covers",
    description: "Copies local cover images onto this device.",
    icon: IconFolder,
  },
];

const externalSource = ref<ExternalLibrarySource>("f95checker");
const externalPath = ref("");
const externalDetectedPath = ref("");
const externalCheckedPaths = ref<string[]>([]);
const externalDetecting = ref(false);
const externalDetectedSources = new Set<ExternalLibrarySource>();
const externalPreview = ref<ExternalLibraryInspectResponse | null>(null);
const externalSections = ref<LibraryBackupSection[]>([]);
const externalMergeMode = ref<ExternalLibraryMergeMode>("fill_missing");
const externalMessage = ref("");
const externalError = ref("");
const externalInspecting = ref(false);
const externalImporting = ref(false);
const externalResult = ref<ExternalLibraryImportResponse | null>(null);
const browsing = ref(false);

// Bumped when the path or source changes so a late answer for the previous
// file/source cannot populate the new preview.
let externalDetectRequest = 0;
let externalInspectRequest = 0;

const externalCounts = computed(() => externalPreview.value?.counts || null);
const externalCoversToCopy = computed(() => {
  const counts = externalCounts.value;
  if (externalMergeMode.value === "fill_missing") {
    return counts?.covers_to_fill ?? counts?.covers_to_copy ?? 0;
  }
  return counts?.covers_to_copy ?? 0;
});
const externalAvailableSections = computed<LibraryBackupSection[]>(
  () => externalPreview.value?.available_sections || [],
);
const externalWarnings = computed<LibraryBackupWarning[]>(
  () => externalPreview.value?.warnings || [],
);
const externalAvailableOptions = computed(() => {
  return externalSectionOptions.filter(
    (item) =>
      externalImportSectionIds.includes(item.id) &&
      externalAvailableSections.value.includes(item.id),
  );
});
const canImportExternal = computed(() => {
  // Identity and versions always import, so an empty section list is valid.
  return Boolean(externalPreview.value);
});

const isSelected = (id: LibraryBackupSection) => externalSections.value.includes(id);

const toggleExternalSection = (id: LibraryBackupSection) => {
  if (externalSections.value.includes(id)) {
    externalSections.value = externalSections.value.filter((section) => section !== id);
  } else {
    externalSections.value = [...externalSections.value, id];
  }
};

const detectExternal = async () => {
  if (externalDetecting.value) return;
  const source = externalSource.value;
  const requestId = ++externalDetectRequest;
  const initialPath = externalPath.value;
  externalError.value = "";
  externalDetecting.value = true;
  try {
    const result = await api.detectExternalLibrary(source);
    if (requestId !== externalDetectRequest || externalSource.value !== source) return;
    if (!result || result.success === false) {
      externalError.value = result?.error || "Could not locate the source library.";
      return;
    }
    externalDetectedPath.value = result.path || "";
    externalCheckedPaths.value = result.checked || [];
    externalDetectedSources.add(source);
    // Never overwrite a path the user typed while detection was running.
    if (result.path && externalPath.value === initialPath) externalPath.value = result.path;
  } catch (error) {
    if (requestId !== externalDetectRequest || externalSource.value !== source) return;
    externalError.value = `Could not locate the source library: ${String(error)}`;
  } finally {
    if (requestId === externalDetectRequest) externalDetecting.value = false;
  }
};

const browseExternalFile = async () => {
  if (browsing.value) return;
  const source = externalSource.value;
  browsing.value = true;
  try {
    const selected = await api.browseExternalLibraryFile(
      source,
      externalPath.value || "",
    );
    if (selected && externalSource.value === source) {
      externalPath.value = selected;
      externalPreview.value = null;
      externalResult.value = null;
      externalMessage.value = "";
      externalError.value = "";
    }
  } catch (error) {
    externalError.value = `Could not open library picker: ${String(error)}`;
  } finally {
    browsing.value = false;
  }
};

const inspectExternal = async () => {
  if (externalInspecting.value) return;
  const source = externalSource.value;
  const path = externalPath.value;
  const requestId = ++externalInspectRequest;
  externalMessage.value = "";
  externalError.value = "";
  externalPreview.value = null;
  externalResult.value = null;
  externalInspecting.value = true;

  try {
    const result = await api.inspectExternalLibrary(source, path);
    if (
      requestId !== externalInspectRequest ||
      externalSource.value !== source ||
      externalPath.value !== path
    ) {
      return;
    }
    if (!result || result.success === false) {
      externalError.value = result?.error_code === "source_locked"
        ? "Close Playnite and try again."
        : result?.error || "Could not inspect library.";
      return;
    }
    externalPreview.value = result;
    externalSections.value = (result.available_sections || []).filter(
      (section): section is LibraryBackupSection =>
        externalImportSectionIds.includes(section),
    );
  } catch (error) {
    if (
      requestId !== externalInspectRequest ||
      externalSource.value !== source ||
      externalPath.value !== path
    ) {
      return;
    }
    externalError.value = `Could not inspect library: ${String(error)}`;
  } finally {
    if (requestId === externalInspectRequest) externalInspecting.value = false;
  }
};

const runExternalImport = async () => {
  if (externalImporting.value || !externalPreview.value) return;
  externalMessage.value = "";
  externalError.value = "";
  externalResult.value = null;
  externalImporting.value = true;
  emit("busy", true);

  try {
    const result = await api.importExternalLibrary(externalSource.value, externalPath.value, {
      sections: externalSections.value,
      merge_mode: externalMergeMode.value,
    });
    if (!result || result.success === false) {
      externalError.value = result?.error || "Import failed.";
      return;
    }
    externalResult.value = result;
    externalMessage.value =
      `Imported library. Created ${result.created ?? 0}, ` +
      `updated ${result.updated ?? 0}, skipped ${result.skipped ?? 0}.`;
    window.dispatchEvent(new Event("wlib-refresh-library"));
    externalPreview.value = null;
    emit("imported", result);
  } catch (error) {
    externalError.value = `Import failed: ${String(error)}`;
  } finally {
    externalImporting.value = false;
    emit("busy", false);
  }
};

watch(externalSource, () => {
  externalDetectRequest++;
  externalDetecting.value = false;
  externalInspectRequest++;
  externalInspecting.value = false;
  externalPath.value = "";
  externalDetectedPath.value = "";
  externalCheckedPaths.value = [];
  externalPreview.value = null;
  externalResult.value = null;
  externalSections.value = [];
  externalMessage.value = "";
  externalError.value = "";
  void detectExternal();
});

watch(externalPath, () => {
  externalInspectRequest++;
  externalInspecting.value = false;
  externalPreview.value = null;
  externalResult.value = null;
  externalMessage.value = "";
  externalError.value = "";
});

onMounted(() => {
  if (!externalDetectedSources.has(externalSource.value)) {
    void detectExternal();
  }
});
</script>

<template>
  <div
    class="external-import-panel"
    :class="{ 'external-import-panel--embedded': props.variant === 'embedded' }"
  >
    <section class="backup-body">
      <h3 v-if="props.variant !== 'embedded'" class="sr-only">From another app</h3>

      <div class="backup-segmented" role="group" aria-label="Source app">
        <button
          v-for="item in externalSources"
          :key="item.id"
          type="button"
          :class="['backup-tab', externalSource === item.id ? 'backup-tab-active' : '']"
          :disabled="externalImporting"
          @click="externalSource = item.id"
        >
          {{ item.label }}
        </button>
      </div>

      <div v-if="externalDetecting" class="backup-note">
        <IconLoader2 class="backup-spinner" />
        <span>Looking for the source library...</span>
      </div>
      <div v-else-if="externalDetectedPath" class="backup-note">
        <IconInfoCircle />
        <span>Found library: {{ externalDetectedPath }}</span>
      </div>
      <div v-else-if="externalCheckedPaths.length" class="backup-note">
        <IconInfoCircle />
        <span>
          No library found. Checked:
          {{ externalCheckedPaths.join(", ") }}.
          Pick the file manually.
        </span>
      </div>

      <div class="backup-field">
        <label>Source library file</label>
        <div class="backup-path-row">
          <input
            v-model="externalPath"
            type="text"
            :placeholder="externalSource === 'gamelist' ? 'Path to Games.fic' : 'Path to the source library file'"
            class="backup-input"
            :disabled="externalImporting"
          />
          <button
            type="button"
            class="backup-secondary"
            :disabled="externalImporting"
            @click="browseExternalFile"
          >
            <IconFolderOpen />
            Browse
          </button>
        </div>
        <PathStatus :path="externalPath" kind="file" />
      </div>

      <button
        type="button"
        class="backup-secondary backup-inspect"
        :disabled="externalInspecting || externalImporting || !externalPath"
        @click="inspectExternal"
      >
        <IconLoader2 v-if="externalInspecting" class="backup-spinner" />
        <IconFileImport v-else />
        {{ externalInspecting ? "Inspecting..." : "Inspect Library" }}
      </button>

      <div v-if="externalPreview" class="backup-preview">
        <div class="backup-counts">
          <span>New: {{ externalCounts?.new_games ?? 0 }}</span>
          <span>Matched: {{ externalCounts?.matched_games ?? 0 }}</span>
          <span>Ambiguous: {{ externalCounts?.ambiguous_games ?? 0 }}</span>
          <span>Skipped: {{ externalCounts?.skipped_no_identity ?? 0 }}</span>
          <span v-if="(externalCounts?.skipped_unreadable ?? 0) > 0">Unreadable: {{ externalCounts?.skipped_unreadable }}</span>
          <span>Paths dropped: {{ externalCounts?.dropped_paths ?? 0 }}</span>
          <span v-if="externalAvailableSections.includes('covers')">Covers to copy: {{ externalCoversToCopy }}</span>
        </div>

        <div class="backup-field">
          <label>For games already in wLib</label>
          <div class="backup-segmented" role="radiogroup" aria-label="Merge mode">
            <label class="backup-check-row">
              <input
                v-model="externalMergeMode"
                type="radio"
                value="fill_missing"
                :disabled="externalImporting"
              />
              <span class="backup-option-copy">
                <strong>Only fill missing</strong>
                <em>Keeps your values and fills in blanks.</em>
              </span>
            </label>
            <label class="backup-check-row">
              <input
                v-model="externalMergeMode"
                type="radio"
                value="overwrite"
                :disabled="externalImporting"
              />
              <span class="backup-option-copy">
                <strong>Overwrite</strong>
                <em>Replaces your values with the source's.</em>
              </span>
            </label>
          </div>
        </div>

        <div class="backup-section-list compact">
          <label
            v-for="option in externalAvailableOptions"
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
              :checked="isSelected(option.id)"
              :disabled="externalImporting"
              @change="toggleExternalSection(option.id)"
            />
          </label>
        </div>

        <div class="backup-disclaimer">
          <IconInfoCircle />
          <span>
            Titles, F95 links, and versions are always imported. The
            sections above choose what else comes along.
          </span>
        </div>

        <div v-if="externalWarnings.length" class="backup-warnings">
          <strong>
            <IconAlertTriangle />
            {{ externalWarnings.length }} warning{{ externalWarnings.length === 1 ? '' : 's' }}
          </strong>
          <ul>
            <li v-for="(warning, index) in externalWarnings" :key="index">
              {{ warning.message }}
            </li>
          </ul>
        </div>
      </div>
    </section>

    <footer class="backup-footer">
      <div class="backup-feedback">
        <p v-if="externalError" class="backup-error" role="alert">
          {{ externalError }}
        </p>
        <div v-else-if="externalResult" class="backup-success" role="status">
          <p>{{ externalMessage }}</p>
          <p v-if="(externalResult.skipped_unreadable ?? 0) > 0">
            Unreadable: {{ externalResult.skipped_unreadable }}
          </p>
          <router-link
            v-if="props.variant !== 'embedded' && externalResult.metadata_check === 'started'"
            to="/updates"
          >
            View progress
          </router-link>
          <p v-else-if="externalResult.metadata_check === 'busy'">
            An update check is already running; run one later from Updates.
          </p>
        </div>
      </div>

      <div class="backup-actions">
        <button
          type="button"
          class="backup-primary"
          :disabled="externalImporting || !canImportExternal"
          @click="runExternalImport"
        >
          <IconLoader2 v-if="externalImporting" class="backup-spinner" />
          <IconFileImport v-else />
          {{ externalImporting ? "Importing..." : "Import Selected" }}
        </button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.external-import-panel {
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
}

.external-import-panel button:focus-visible,
.external-import-panel input[type="checkbox"]:focus-visible,
.external-import-panel input[type="radio"]:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
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

.external-import-panel--embedded .backup-body {
  padding: 1rem;
  gap: 0.875rem;
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

@media (max-width: 640px) {
  .backup-path-row {
    flex-wrap: wrap;
  }
  .backup-path-row input {
    flex-basis: 100%;
  }
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

.backup-warnings {
  display: block;
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
  padding: 1rem 1.25rem;
  border-top: 1px solid var(--border);
  background: var(--bg-inset);
  display: flex;
  gap: 1rem;
  align-items: center;
  justify-content: space-between;
  border-radius: 0 0 0.75rem 0.75rem;
}

.external-import-panel--embedded .backup-footer {
  padding: 0.75rem 1rem;
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
