// SPDX-License-Identifier: GPL-3.0-or-later
import type { RpgmDatabaseEntry, RpgmPatch, RpgmValueRow } from "../services/api";

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
  hits.sort((a, b) => Number(!b.name.toLowerCase().startsWith(needle)) - Number(!a.name.toLowerCase().startsWith(needle)) || a.id - b.id);
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

// The value a patch writes, for showing pending edits in the Raw tree.
export const patchValue = (patch: RpgmPatch): unknown => (patch.op === "set_count" ? patch.count : patch.value);
