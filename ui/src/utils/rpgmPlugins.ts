// SPDX-License-Identifier: GPL-3.0-or-later
import type { RpgmPluginEntry, RpgmPluginIssue, RpgmPluginsState } from "../services/api";

export interface PluginConfirm {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
}

/** Entries whose name or description contains the query, letter case ignored. */
export const filterPlugins = (entries: RpgmPluginEntry[], query: string): RpgmPluginEntry[] => {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return entries;
  return entries.filter(
    (entry) =>
      entry.name.toLocaleLowerCase().includes(needle) || entry.description.toLocaleLowerCase().includes(needle),
  );
};

export const pluginCounts = (entries: RpgmPluginEntry[]): { on: number; off: number } => {
  const on = entries.filter((entry) => entry.enabled).length;
  return { on, off: entries.length - on };
};

const joinNames = (names: string[]): string => names.join(", ");

/** One line of text for an issue; the level label is shown apart from it. */
export const issueText = (issue: RpgmPluginIssue): string => {
  switch (issue.code) {
    case "missing_file":
      return issue.level === "warning"
        ? "File is missing. The game stops when it tries to load this plugin."
        : "File is missing. It cannot be switched on.";
    case "unsafe_name":
      return "Unsafe name. wLib does not read its file and does not switch it on.";
    case "missing_dependency":
      return `Needs ${joinNames(issue.names)}, which ${issue.names.length === 1 ? "does" : "do"} not load.`;
    case "wrong_order":
      return `Must load ${issue.direction === "before" ? "before" : "after"} ${joinNames(issue.names)}.`;
    case "case_mismatch":
      return "Letter case of the file name differs. It can fail to load on case-sensitive file systems.";
    case "duplicate":
      return "Not loaded, duplicate. An earlier entry of the same plugin loads.";
    default:
      return "Unknown issue.";
  }
};

export const issueLabel = (issue: RpgmPluginIssue): string => (issue.level === "warning" ? "Warning" : "Note");

/** Whether a row's switch can be used, and why not. */
export const pluginSwitchState = (entry: RpgmPluginEntry, busy: boolean): { enabled: boolean; reason: string } => {
  if (!entry.enabled && !entry.can_enable) {
    const unsafe = entry.issues.some((issue) => issue.code === "unsafe_name");
    return { enabled: false, reason: unsafe ? "Its name is unsafe" : "Its file is missing" };
  }
  if (busy) return { enabled: false, reason: "" };
  return { enabled: true, reason: "" };
};

export interface PluginRestoreState {
  visible: boolean;
  enabled: boolean;
  confirm: boolean;
  // Why Restore is not offered, when that needs saying (a damaged backup).
  reason: string;
}

export const pluginRestoreState = (state: RpgmPluginsState, busy: boolean): PluginRestoreState => {
  const backup = state.backup ?? { state: "none", reason: "" };
  switch (backup.state) {
    case "toggles":
      return { visible: true, enabled: !busy, confirm: false, reason: "" };
    case "changed":
      return { visible: true, enabled: !busy, confirm: true, reason: "" };
    case "invalid":
      return {
        visible: false,
        enabled: false,
        confirm: false,
        reason: `Restore is not available. ${backup.reason || "The backup cannot be used."}`,
      };
    default:
      return { visible: false, enabled: false, confirm: false, reason: "" };
  }
};

/** The confirmation before a switch, or null when the switch runs at once. */
export const switchConfirmation = (
  state: RpgmPluginsState,
  entry: RpgmPluginEntry,
  enabled: boolean,
): PluginConfirm | null => {
  const saveWarning = (state.backup?.state ?? "none") === "none";
  const needed = !enabled && entry.loaded && state.engine === "mz" ? entry.needed_by : [];
  if (!saveWarning && needed.length === 0) return null;
  const parts: string[] = [];
  if (needed.length) {
    parts.push(
      `${needed.length} loaded ${needed.length === 1 ? "plugin declares" : "plugins declare"} that ${needed.length === 1 ? "it needs" : "they need"} ${entry.name}: ${joinNames(needed)}. ${needed.length === 1 ? "It" : "They"} can stop working, and the game can stop with an error.`,
    );
  }
  if (saveWarning) {
    parts.push(
      "Plugin changes can break existing saves. Back up the saves first if you want to keep them. wLib keeps a copy of the plugin list, so you can restore it.",
    );
  }
  return {
    title: `Switch ${enabled ? "on" : "off"} ${entry.name}?`,
    message: parts.join("\n\n"),
    confirmLabel: enabled ? "Switch on" : "Switch off",
    danger: needed.length > 0,
  };
};

/** The confirmation before a restore, or null when it runs at once. */
export const restoreConfirmation = (state: RpgmPluginsState): PluginConfirm | null => {
  if (state.backup?.state !== "changed") return null;
  return {
    title: "Restore the plugin list?",
    message:
      "The current plugin list has other changes than on/off values, or it cannot be read. " +
      "A game update can cause this. Restore replaces the list with the backup, and those changes will be lost.",
    confirmLabel: "Restore",
    danger: true,
  };
};
