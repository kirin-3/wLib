// SPDX-License-Identifier: GPL-3.0-or-later
import type { RgssRawNodeData, RpgmDatabaseEntry, RpgmPatch, RpgmSaveFormat, RpgmValueRow } from "../services/api";

export const MAX_GOLD = 9_999_999;
export const MAX_COUNT = 9_999;
export const MAX_LEVEL = 9_999;
export const MAX_STAT = 99_999_999;
export const MAX_BONUS = 9_999_999;
export const MAX_EXP = Number.MAX_SAFE_INTEGER;

export type RpgmPath = (string | number)[];
export type PatchMap = Map<string, RpgmPatch>;

export type Validated<T> = { ok: true; value: T } | { ok: false; error: string };

// Curated and Raw edits of the same leaf share this key, so the last edit wins.
export const pathKey = (path: RpgmPath): string => JSON.stringify(path);
export const refKey = (id: number): string => `ref:${id}`;
export const leafKey = (ref: number | null | undefined, fallback: RpgmPath): string =>
  ref !== null && ref !== undefined ? refKey(ref) : pathKey(fallback);

export const isRgssFormat = (format: RpgmSaveFormat | undefined): boolean =>
  format === "xp" || format === "vx" || format === "vxace";

export const formatLabel = (format: RpgmSaveFormat | undefined): string => {
  switch (format) {
    case "xp":
      return "XP";
    case "vx":
      return "VX";
    case "vxace":
      return "VX Ace";
    case "mv":
      return "MV";
    case "mz":
      return "MZ";
    default:
      return "";
  }
};

export const setPatch = (map: PatchMap, key: string, patch: RpgmPatch): PatchMap => {
  const next = new Map(map);
  next.set(key, patch);
  return next;
};

export const clearPatch = (map: PatchMap, key: string): PatchMap => {
  const next = new Map(map);
  next.delete(key);
  return next;
};

// Level edits go first: the backend recalculates experience for them, and an
// explicit experience edit made afterwards must win.
export const buildPatchList = (map: PatchMap): RpgmPatch[] => {
  const all = [...map.values()];
  const isLevel = (patch: RpgmPatch): boolean => patch.op === "set_actor" && patch.field === "level";
  return [...all.filter(isLevel), ...all.filter((patch) => !isLevel(patch))];
};

export const isDirty = (map: PatchMap): boolean => map.size > 0;

const parseWhole = (text: string | number, low: number, high: number, label: string): Validated<number> => {
  const raw = typeof text === "number" ? String(text) : text.trim();
  if (!/^-?\d+$/.test(raw)) return { ok: false, error: `${label} must be a whole number` };
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < low || value > high) {
    return { ok: false, error: `${label} must be from ${low.toLocaleString("en-US")} to ${high.toLocaleString("en-US")}` };
  }
  return { ok: true, value };
};

export const validateGold = (text: string | number): Validated<number> => parseWhole(text, 0, MAX_GOLD, "Gold");
export const validateCount = (text: string | number): Validated<number> => parseWhole(text, 0, MAX_COUNT, "Count");
export const validateLevel = (text: string | number): Validated<number> => parseWhole(text, 1, MAX_LEVEL, "Level");
export const validateExp = (text: string | number): Validated<number> => parseWhole(text, 0, MAX_EXP, "Experience");
export const validateStat = (text: string | number): Validated<number> => parseWhole(text, 0, MAX_STAT, "Value");
export const validateBonus = (text: string | number): Validated<number> => parseWhole(text, -MAX_BONUS, MAX_BONUS, "Bonus");

export const validateName = (text: string): Validated<string> => {
  if (!text.trim()) return { ok: false, error: "The name cannot be empty" };
  if (text.length > 200) return { ok: false, error: "The name is too long" };
  return { ok: true, value: text };
};

// A variable is a number or text, matching the type it already holds.
export const validateVariable = (text: string, type: RpgmValueRow["type"]): Validated<number | string> => {
  if (type === "string") return { ok: true, value: text };
  if (type !== "number") return { ok: false, error: "This variable cannot be edited" };
  const raw = text.trim();
  if (raw === "" || !Number.isFinite(Number(raw))) return { ok: false, error: "Enter a number" };
  const value = Number(raw);
  if (Math.abs(value) > Number.MAX_SAFE_INTEGER) return { ok: false, error: "The number is too large" };
  return { ok: true, value };
};

// Picker: entries with a name, best matches (starts-with) first, held ones kept.
export const filterPicker = (entries: RpgmDatabaseEntry[], query: string, limit = 50): RpgmDatabaseEntry[] => {
  const needle = query.trim().toLowerCase();
  const named = entries.filter((entry) => entry.name.trim() !== "");
  if (!needle) return named.slice(0, limit);
  const hits = named.filter((entry) => entry.name.toLowerCase().includes(needle) || String(entry.id) === needle);
  hits.sort((a, b) => Number(!a.name.toLowerCase().startsWith(needle)) - Number(!b.name.toLowerCase().startsWith(needle)) || a.id - b.id);
  return hits.slice(0, limit);
};

export const filterRows = <T extends { id: number; name: string }>(rows: T[], query: string): T[] => {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) => String(row.id) === needle || String(row.id).startsWith(needle) || row.name.toLowerCase().includes(needle));
};

export const badgeForKind = (kind: RpgmDatabaseEntry["kind"]): string => (kind === "key" ? "Key item" : kind === "hidden" ? "Hidden" : "");

// ---- Raw tree ----

export type RawScalar = string | number | boolean;

export const isRawScalar = (value: unknown): value is RawScalar =>
  typeof value === "string" || typeof value === "number" || typeof value === "boolean";

export const isEditableRaw = (key: string | number, value: unknown): boolean =>
  !(typeof key === "string" && key.startsWith("@")) && isRawScalar(value);

export interface RawChild {
  key: string | number;
  value: unknown;
  // JsonEx bookkeeping (`@`, `@c`): shown but never editable
  meta: boolean;
  editable: boolean;
  container: boolean;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

// Children of a node. A JsonEx `{"@a": [...]}` array wrapper is shown as the array itself.
export const rawChildren = (node: unknown): RawChild[] => {
  let source = node;
  const meta: RawChild[] = [];
  if (isObject(source) && Array.isArray(source["@a"])) {
    for (const [key, value] of Object.entries(source)) {
      if (key.startsWith("@") && key !== "@a") meta.push({ key, value, meta: true, editable: false, container: false });
    }
    source = source["@a"];
  }
  const rows: RawChild[] = Array.isArray(source)
    ? source.map((value, index) => ({ key: index, value, meta: false, editable: isEditableRaw(index, value), container: typeof value === "object" && value !== null }))
    : isObject(source)
      ? Object.entries(source).map(([key, value]) => ({
          key,
          value,
          meta: key.startsWith("@"),
          editable: isEditableRaw(key, value),
          container: typeof value === "object" && value !== null,
        }))
      : [];
  return [...meta, ...rows];
};

// Parse text typed for a Raw leaf so it keeps the type of the original value.
export const parseRawInput = (original: RawScalar, text: string): Validated<RawScalar> => {
  if (typeof original === "string") return { ok: true, value: text };
  if (typeof original === "boolean") {
    const lowered = text.trim().toLowerCase();
    if (lowered === "true") return { ok: true, value: true };
    if (lowered === "false") return { ok: true, value: false };
    return { ok: false, error: "Enter true or false" };
  }
  const raw = text.trim();
  if (raw === "" || !Number.isFinite(Number(raw))) return { ok: false, error: "Enter a number" };
  const value = Number(raw);
  if (Number.isInteger(original) && !Number.isInteger(value)) return { ok: false, error: "This value must be a whole number" };
  if (Math.abs(value) > Number.MAX_SAFE_INTEGER) return { ok: false, error: "The number is too large" };
  return { ok: true, value };
};

// Parse text typed for an RGSS Raw scalar, strictly preserving the Marshal type
// (unlike JS numbers, Ruby distinguishes Integer `1` from Float `1.0`).
export type RgssScalarType = "int" | "float" | "bool" | "str";

export const parseRgssRawInput = (type: RgssScalarType, text: string): Validated<RawScalar> => {
  if (type === "str") return { ok: true, value: text };
  if (type === "bool") {
    const lowered = text.trim().toLowerCase();
    if (lowered === "true") return { ok: true, value: true };
    if (lowered === "false") return { ok: true, value: false };
    return { ok: false, error: "Enter true or false" };
  }
  const raw = text.trim();
  if (type === "int") {
    if (!/^-?\d+$/.test(raw)) return { ok: false, error: "This value must be a whole number" };
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) return { ok: false, error: "The number is too large" };
    return { ok: true, value };
  }
  if (raw === "" || !Number.isFinite(Number(raw))) return { ok: false, error: "Enter a number" };
  const value = Number(raw);
  if (Math.abs(value) > Number.MAX_SAFE_INTEGER) return { ok: false, error: "The number is too large" };
  return { ok: true, value };
};

// The value a patch writes, for showing pending edits in the Raw tree.
export const patchValue = (patch: RpgmPatch): unknown => (patch.op === "set_count" ? patch.count : patch.value);

// ---- Change review ----

// What the review list shows for one pending edit. `before` is the value the
// save held when it was opened; the new value comes from the patch itself.
export interface ChangeMeta {
  label: string;
  before: unknown;
  // Switches read as ON/OFF; every other true/false value as true/false.
  kind?: "switch";
}
export type ChangeMetaMap = Map<string, ChangeMeta>;

export interface PendingChange {
  key: string;
  label: string;
  before: string;
  after: string;
}

export const formatChangeValue = (value: unknown, kind?: ChangeMeta["kind"]): string => {
  if (typeof value === "boolean") return kind === "switch" ? (value ? "ON" : "OFF") : String(value);
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("en-US") : String(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (value === null || value === undefined) return "(none)";
  return String(value);
};

// Pending edits in the order they were first made. A patch without a label
// still shows, so the list always matches the count on the Save button.
export const summarizeChanges = (patches: PatchMap, meta: ChangeMetaMap): PendingChange[] =>
  [...patches.entries()].map(([key, patch]) => {
    const info = meta.get(key);
    return {
      key,
      label: info?.label ?? key,
      before: formatChangeValue(info?.before, info?.kind),
      after: formatChangeValue(patchValue(patch), info?.kind),
    };
  });

export const countChanges = (count: number): string => `${count.toLocaleString("en-US")} ${count === 1 ? "change" : "changes"}`;

export const saveButtonLabel = (count: number): string => (count > 0 ? `Save ${countChanges(count)}` : "Save");

// ---- Field drafts ----

// The editor's sub-tabs, in the order they show.
export type EditorTab = "party" | "actors" | "switches" | "variables" | "raw";

// What the user typed in one field. A field shows its draft text, even when the
// text is not valid, so a bad entry does not snap back to the old value. A valid
// draft has `error: null`; its value is already a pending change (or matches
// the save). An invalid draft is never a pending change.
export interface FieldDraft {
  text: string;
  error: string | null;
  tab: EditorTab;
}
export type DraftMap = Map<string, FieldDraft>;

export const setDraft = (map: DraftMap, id: string, text: string, tab: EditorTab, result: Validated<unknown>): DraftMap => {
  const next = new Map(map);
  next.set(id, { text, error: result.ok ? null : result.error, tab });
  return next;
};

// When the field loses focus, a valid draft is dropped so the field shows the
// value as the save will hold it ("007" becomes "7"). An invalid one stays.
export const settleDraft = (map: DraftMap, id: string): DraftMap => {
  const draft = map.get(id);
  if (!draft || draft.error !== null) return map;
  const next = new Map(map);
  next.delete(id);
  return next;
};

export const draftText = (map: DraftMap, id: string, fallback: unknown): string => {
  const draft = map.get(id);
  if (draft) return draft.text;
  return fallback === null || fallback === undefined ? "" : String(fallback);
};

export const draftError = (map: DraftMap, id: string): string => map.get(id)?.error ?? "";

export const countInvalid = (map: DraftMap): number => [...map.values()].filter((draft) => draft.error !== null).length;

export const invalidByTab = (map: DraftMap): Partial<Record<EditorTab, number>> => {
  const counts: Partial<Record<EditorTab, number>> = {};
  for (const draft of map.values()) {
    if (draft.error !== null) counts[draft.tab] = (counts[draft.tab] ?? 0) + 1;
  }
  return counts;
};

export const firstInvalidTab = (map: DraftMap, order: EditorTab[]): EditorTab | null => {
  const counts = invalidByTab(map);
  return order.find((tab) => (counts[tab] ?? 0) > 0) ?? null;
};

const countFields = (count: number): string => `${count.toLocaleString("en-US")} ${count === 1 ? "field" : "fields"}`;

// Accessible text for the error marker on a sub-tab.
export const tabErrorText = (count: number): string => `${countFields(count)} to fix`;

// "Fix 1 field on Party", "Fix 3 fields on Party and Raw".
export const fixFieldsLabel = (map: DraftMap, order: { id: EditorTab; label: string }[]): string => {
  const counts = invalidByTab(map);
  const tabs = order.filter((entry) => (counts[entry.id] ?? 0) > 0).map((entry) => entry.label);
  if (tabs.length === 0) return "";
  const where = tabs.length === 1 ? tabs[0] : `${tabs.slice(0, -1).join(", ")} and ${tabs[tabs.length - 1]}`;
  return `Fix ${countFields(countInvalid(map))} on ${where}`;
};

// Field ids hold path text (quotes, brackets); DOM ids must stay plain.
const domSafe = (id: string): string => id.replace(/[^A-Za-z0-9_-]/g, (char) => `_${char.codePointAt(0)?.toString(16) ?? ""}_`);
export const fieldDomId = (id: string): string => `rpgm-field-${domSafe(id)}`;
export const fieldErrorId = (id: string): string => `rpgm-error-${domSafe(id)}`;

// Draft ids of Raw tree fields (MV/MZ path, RGSS node).
export const rawFieldId = (path: RpgmPath): string => `raw:${pathKey(path)}`;
export const rgssFieldId = (nodeId: number): string => `rgss:${nodeId}`;

// "party › _questLog › points" for a Raw edit of an MV/MZ save.
export const rawPathLabel = (path: RpgmPath): string => path.map(String).join(" › ");

const rgssKeyLabel = (node: RgssRawNodeData): string => {
  switch (node.t) {
    case "sym":
      return `:${node.v}`;
    case "str":
      return JSON.stringify(node.v);
    case "int":
    case "float":
    case "bool":
      return String(node.v);
    default:
      return `#${node.id}`;
  }
};

const rgssChildren = (node: RgssRawNodeData): [string, RgssRawNodeData][] => {
  switch (node.t) {
    case "object":
    case "struct":
      return node.ivars;
    case "array":
      return node.items.map((item, index) => [`[${index}]`, item]);
    case "hash": {
      const rows: [string, RgssRawNodeData][] = node.pairs.map(([key, value]) => [rgssKeyLabel(key), value]);
      if (node.default) rows.push(["default", node.default]);
      return rows;
    }
    case "usermarshal":
      return [["value", node.value]];
    case "extended":
    case "userclass":
      return [["inner", node.inner]];
    default:
      return [];
  }
};

// Where an RGSS Raw node sits, as "Contents › @party › @gold". The tree has
// no cycles: shared objects appear once and are linked by id elsewhere.
export const rgssNodeLabel = (entries: { label: string; node: RgssRawNodeData }[], nodeId: number): string => {
  const walk = (node: RgssRawNodeData, trail: string[]): string[] | null => {
    if (node.id === nodeId) return trail;
    for (const [label, child] of rgssChildren(node)) {
      const found = walk(child, [...trail, label]);
      if (found) return found;
    }
    return null;
  };
  for (const entry of entries) {
    const found = walk(entry.node, [entry.label]);
    if (found) return found.join(" › ");
  }
  return `Node #${nodeId}`;
};
