<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch } from "vue";
import { IconAlertCircle, IconChevronDown, IconCircleCheck, IconLoader2, IconPlus, IconX } from "@tabler/icons-vue";
import { api, type RgssRawNodeData, type RpgmActorView, type RpgmInventoryKind, type RpgmPatch, type RpgmSaveLoadResponse, type RpgmValueRow } from "../../services/api";
import { confirmAction } from "../../utils/confirm";
import { notify, notifyError } from "../../utils/toast";
import { rovingIndex, useModalKeyboard } from "../../utils/modalKeyboard";
import { refreshRunningGames, runningGameIds } from "../../composables/useRunningGames";
import RpgmRawNode from "../RpgmRawNode.vue";
import RgssRawNode, { type RgssRawEntry } from "../RgssRawNode.vue";
import {
  badgeForKind,
  buildPatchList,
  clearPatch,
  countChanges,
  countInvalid,
  draftError,
  draftText,
  fieldDomId,
  fieldErrorId,
  filterPicker,
  filterRows,
  firstInvalidTab,
  fixFieldsLabel,
  formatLabel,
  invalidByTab,
  isDirty,
  isRgssFormat,
  leafKey,
  patchValue,
  pathKey,
  rawPathLabel,
  refKey,
  rgssNodeLabel,
  parseRawInput,
  parseRgssRawInput,
  rawFieldId,
  rgssFieldId,
  saveButtonLabel,
  setDraft,
  setPatch,
  settleDraft,
  summarizeChanges,
  tabErrorText,
  validateBonus,
  validateCount,
  validateExp,
  validateGold,
  validateLevel,
  validateName,
  validateStat,
  validateVariable,
  type ChangeMeta,
  type ChangeMetaMap,
  type DraftMap,
  type EditorTab,
  type PatchMap,
  type RawScalar,
  type RgssScalarType,
  type RpgmPath,
  type Validated,
} from "../../utils/rpgmSaveEditor";

const isOpen = defineModel<boolean>({ required: true });
const props = defineProps<{ gameId: number; file: string }>();
const emit = defineEmits<{ saved: [backup: string] }>();

type Tab = EditorTab;
const TABS: { id: Tab; label: string }[] = [
  { id: "party", label: "Party" },
  { id: "actors", label: "Actors" },
  { id: "switches", label: "Switches" },
  { id: "variables", label: "Variables" },
  { id: "raw", label: "Raw" },
];
const KIND_KEY: Record<RpgmInventoryKind, string> = { item: "_items", weapon: "_weapons", armor: "_armors" };
const KIND_LABEL: Record<RpgmInventoryKind, string> = { item: "Items", weapon: "Weapons", armor: "Armors" };
const KIND_SINGULAR: Record<RpgmInventoryKind, string> = { item: "Item", weapon: "Weapon", armor: "Armor" };

const modalRef = useTemplateRef<HTMLElement>("modalRef");
const panelRef = useTemplateRef<HTMLElement>("panelRef");
const loading = ref(false);
const loadError = ref("");
const saveError = ref("");
const saving = ref(false);
const save = ref<RpgmSaveLoadResponse | null>(null);
const patches = ref<PatchMap>(new Map());
// Label and original value of each pending edit, for the review list
const changeMeta = ref<ChangeMetaMap>(new Map());
const reviewOpen = ref(false);
// The last write, kept until the next edit so it can be undone
const lastSaved = ref<{ backup: string; count: number } | null>(null);
const restoring = ref(false);
const restoreError = ref("");
// Text typed in each field, kept until the field is valid and loses focus
const drafts = ref<DraftMap>(new Map());
const tab = ref<Tab>("party");
const switchFilter = ref("");
const variableFilter = ref("");
const pickerKind = ref<RpgmInventoryKind>("item");
const pickerQuery = ref("");
// Entries added from the picker that the save does not hold yet
const added = ref<{ kind: RpgmInventoryKind; id: number; name: string }[]>([]);
let loadToken = 0;
// Games can name thousands of switches; render a page at a time.
const ROW_PAGE = 300;
const switchLimit = ref(ROW_PAGE);
const variableLimit = ref(ROW_PAGE);

const dirty = computed(() => isDirty(patches.value));
const changes = computed(() => summarizeChanges(patches.value, changeMeta.value));
// The backend refuses a write while the game runs; say so before Save is pressed.
const gameRunning = computed(() => runningGameIds.value.has(props.gameId));
const busy = computed(() => saving.value || restoring.value);
const hasErrors = computed(() => countInvalid(drafts.value) > 0);
const tabErrors = computed(() => invalidByTab(drafts.value));
const fixLabel = computed(() => fixFieldsLabel(drafts.value, TABS));
const view = computed(() => save.value?.view ?? null);
const names = computed(() => save.value?.names ?? null);
const isRgss = computed(() => isRgssFormat(save.value?.format));
const rgssDumpEntries = computed<RgssRawEntry[]>(() => {
  if (!save.value || !isRgss.value || !Array.isArray(save.value.raw)) return [];
  const fmt = save.value.format;
  return (save.value.raw as RgssRawNodeData[]).map((dump, index) => ({
    id: `dump:${dump.id}`,
    label: fmt === "vxace" ? (index === 0 ? "Header" : index === 1 ? "Contents" : `Dump ${index}`) : `Dump ${index}`,
    node: dump,
  }));
});

const reset = (): void => {
  patches.value = new Map();
  changeMeta.value = new Map();
  reviewOpen.value = false;
  drafts.value = new Map();
  added.value = [];
  saveError.value = "";
};

const load = async (): Promise<void> => {
  const token = ++loadToken;
  loading.value = true;
  loadError.value = "";
  save.value = null;
  reset();
  try {
    const result = await api.loadRpgmSave(props.gameId, props.file);
    if (token !== loadToken) return;
    if (result.success === false || !("view" in result)) {
      loadError.value = ("error" in result && result.error) || "Could not open this save";
      return;
    }
    save.value = result;
  } catch (error) {
    if (token === loadToken) loadError.value = error instanceof Error ? error.message : "Could not open this save";
  } finally {
    if (token === loadToken) loading.value = false;
  }
};

watch(
  () => [isOpen.value, props.file] as const,
  ([open]) => {
    if (open) {
      tab.value = "party";
      switchLimit.value = ROW_PAGE;
      variableLimit.value = ROW_PAGE;
      switchFilter.value = "";
      variableFilter.value = "";
      pickerQuery.value = "";
      lastSaved.value = null;
      restoreError.value = "";
      void refreshRunningGames(() => api.getRunningGames());
      void load();
    } else {
      loadToken += 1;
    }
  },
  { immediate: true },
);

const close = async (): Promise<void> => {
  if (busy.value) return;
  if (dirty.value) {
    const ok = await confirmAction({
      title: "Discard changes?",
      message: "This save has unsaved changes. Close the editor and lose them?",
      confirmLabel: "Discard",
      danger: true,
    });
    if (!ok) return;
  }
  isOpen.value = false;
};

useModalKeyboard(modalRef, () => isOpen.value, () => void close());

const discard = (): void => {
  reset();
};

// Arrow keys, Home and End move between the sub-tabs (one Tab stop for the row).
const onTabKeydown = (event: KeyboardEvent, index: number): void => {
  const next = rovingIndex(event.key, index, TABS.length);
  if (next === null) return;
  event.preventDefault();
  tab.value = TABS[next]!.id;
  (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
};

// ---- editing ----

const remember = (key: string, info: ChangeMeta | null): void => {
  const next = new Map(changeMeta.value);
  if (info) next.set(key, info);
  else next.delete(key);
  changeMeta.value = next;
};

// Store the patch, or drop it when the value is back to what the save held.
const record = (key: string, patch: RpgmPatch, original: unknown, label: string, kind?: ChangeMeta["kind"]): void => {
  const unchanged = patchValue(patch) === original;
  patches.value = unchanged ? clearPatch(patches.value, key) : setPatch(patches.value, key, patch);
  remember(key, unchanged ? null : { label, before: original, ...(kind ? { kind } : {}) });
};

// A new edit starts a new save: the last one can no longer be undone from here.
watch(dirty, (value) => {
  if (value) {
    lastSaved.value = null;
    restoreError.value = "";
  }
});

const inputValue = (event: Event): string => (event.target as HTMLInputElement).value;

// One editable field: its draft id, the sub-tab that shows it, and the key of
// the pending change its value writes.
interface Field {
  id: string;
  tab: Tab;
  key: string;
}

// Runs on each keystroke. The field keeps the typed text and is checked at
// once, so the error goes as soon as the text is valid again. Invalid text
// drops the field's pending change: the count and the review list hold only
// values that will be written.
const typed = <T,>(field: Field, text: string, result: Validated<T>, apply: (value: T) => void): void => {
  drafts.value = setDraft(drafts.value, field.id, text, field.tab, result);
  if (result.ok) {
    apply(result.value);
  } else if (patches.value.has(field.key)) {
    patches.value = clearPatch(patches.value, field.key);
    remember(field.key, null);
  }
};
const settle = (id: string): void => {
  drafts.value = settleDraft(drafts.value, id);
};
const fieldText = (id: string, fallback: unknown): string => draftText(drafts.value, id, fallback);
const fieldError = (id: string): string => draftError(drafts.value, id);
// aria-invalid and aria-describedby for a field that holds an error
const invalidAttrs = (id: string): Record<string, string> =>
  fieldError(id) ? { "aria-invalid": "true", "aria-describedby": fieldErrorId(id) } : {};

const pending = (key: string): RpgmPatch | undefined => patches.value.get(key);
const pendingValue = <T,>(key: string, original: T): T => {
  const patch = pending(key);
  return patch ? (patchValue(patch) as T) : original;
};

const GOLD_PATH: RpgmPath = ["party", "_gold"];
const goldKey = computed(() => leafKey(view.value?.gold_ref, GOLD_PATH));
const editGold = (event: Event): void => {
  const text = inputValue(event);
  typed({ id: "gold", tab: "party", key: goldKey.value }, text, validateGold(text), (value) => {
    const goldRef = view.value?.gold_ref;
    const patch: RpgmPatch =
      isRgss.value && goldRef !== null && goldRef !== undefined
        ? { op: "set_node", node: goldRef, value }
        : { op: "set", path: GOLD_PATH, value };
    record(goldKey.value, patch, view.value?.gold, names.value?.currency ? `Gold (${names.value.currency})` : "Gold");
  });
};

const countPath = (kind: RpgmInventoryKind, id: number): RpgmPath => ["party", KIND_KEY[kind], String(id)];
const countKey = (kind: RpgmInventoryKind, id: number, ref?: number | null): string => leafKey(ref, countPath(kind, id));
const inventoryRows = (kind: RpgmInventoryKind) => {
  const rows = (view.value?.inventory[kind] ?? []).map((row) => ({ ...row, original: row.count as number | null }));
  for (const extra of added.value) {
    if (extra.kind === kind && !rows.some((row) => row.id === extra.id)) {
      rows.push({ id: extra.id, count: 0, name: extra.name, known: true, ref: null, original: null });
    }
  }
  return rows.sort((a, b) => a.id - b.id);
};
const countFieldId = (kind: RpgmInventoryKind, id: number): string => `count:${kind}:${id}`;
const editCount = (kind: RpgmInventoryKind, id: number, name: string, original: number | null, ref: number | null | undefined, event: Event): void => {
  const text = inputValue(event);
  const key = countKey(kind, id, ref);
  typed({ id: countFieldId(kind, id), tab: "party", key }, text, validateCount(text), (count) =>
    record(key, { op: "set_count", kind, id, count }, original ?? 0, `${KIND_SINGULAR[kind]} · ${name}`),
  );
};

const pickerEntries = computed(() => {
  const list = names.value ? (pickerKind.value === "item" ? names.value.items : pickerKind.value === "weapon" ? names.value.weapons : names.value.armors) : [];
  return filterPicker(list, pickerQuery.value, 30);
});
const addEntry = (id: number, name: string): void => {
  const kind = pickerKind.value;
  const held = inventoryRows(kind).find((row) => row.id === id);
  if (held) {
    notify(`${name} is already in the list`);
    return;
  }
  added.value = [...added.value, { kind, id, name }];
  const key = countKey(kind, id, null);
  patches.value = setPatch(patches.value, key, { op: "set_count", kind, id, count: 1 });
  remember(key, { label: `${KIND_SINGULAR[kind]} · ${name}`, before: 0 });
};

const actorPath = (id: number, leaf: string | number, extra?: string | number): RpgmPath =>
  extra === undefined ? ["actors", "_data", id, leaf] : ["actors", "_data", id, leaf, extra];

type ActorField = "name" | "level" | "exp" | "hp" | "mp";
const LEAF: Record<ActorField, string> = { name: "_name", level: "_level", exp: "_exp", hp: "_hp", mp: "_mp" };
const actorFieldRef = (actor: RpgmActorView, field: ActorField): number | null | undefined => {
  switch (field) {
    case "name":
      return actor.name_ref;
    case "level":
      return actor.level_ref;
    case "exp":
      return actor.exp_ref;
    case "hp":
      return actor.hp_ref;
    case "mp":
      return actor.mp_ref;
  }
};
const FIELD_LABEL: Record<ActorField, string> = { name: "Name", level: "Level", exp: "Experience", hp: "HP", mp: "MP" };
const actorLabel = (actor: RpgmActorView, field: string): string => `${actor.name || `Actor ${actor.id}`} · ${field}`;
const actorKey = (actor: RpgmActorView, field: ActorField): string =>
  leafKey(
    actorFieldRef(actor, field),
    field === "exp" ? actorPath(actor.id, "_exp", String(actor.class_id)) : actorPath(actor.id, LEAF[field]),
  );
const bonusKey = (actor: RpgmActorView, index: number): string =>
  leafKey(actor.param_plus_refs?.[index], actorPath(actor.id, "_paramPlus", index));
const actorFieldId = (actor: RpgmActorView, field: ActorField): string => `actor:${actor.id}:${field}`;
const bonusFieldId = (actor: RpgmActorView, index: number): string => `actor:${actor.id}:bonus:${index}`;
const fieldLabelOf = (field: ActorField): string => (field === "mp" ? names.value?.mp_label || FIELD_LABEL.mp : FIELD_LABEL[field]);
// The curated fields of one actor card, in the order they show
const actorFields = (actor: RpgmActorView) =>
  [
    { field: "name", original: actor.name, shown: actor.name, numeric: false, disabled: isRgss.value && actor.name_ref === null },
    { field: "level", original: actor.level, shown: actor.level ?? 1, numeric: true, disabled: false },
    { field: "exp", original: actor.exp, shown: actor.exp ?? 0, numeric: true, disabled: actor.exp === null },
    { field: "hp", original: actor.hp, shown: actor.hp ?? 0, numeric: true, disabled: false },
    { field: "mp", original: actor.mp, shown: actor.mp ?? 0, numeric: true, disabled: false },
  ] satisfies { field: ActorField; original: unknown; shown: unknown; numeric: boolean; disabled: boolean }[];
const editActor = (actor: RpgmActorView, field: ActorField, original: unknown, event: Event): void => {
  const text = inputValue(event);
  const validators = { name: validateName, level: validateLevel, exp: validateExp, hp: validateStat, mp: validateStat } as const;
  const key = actorKey(actor, field);
  typed<string | number>({ id: actorFieldId(actor, field), tab: "actors", key }, text, validators[field](text), (value) =>
    record(key, { op: "set_actor", id: actor.id, field, value }, original, actorLabel(actor, fieldLabelOf(field))),
  );
};
const editBonus = (actor: RpgmActorView, index: number, original: number, event: Event): void => {
  const text = inputValue(event);
  const key = bonusKey(actor, index);
  const param = names.value?.params[index] || `Parameter ${index + 1}`;
  typed({ id: bonusFieldId(actor, index), tab: "actors", key }, text, validateBonus(text), (value) =>
    record(key, { op: "set_actor", id: actor.id, field: "paramPlus", index, value }, original, actorLabel(actor, `${param} bonus`)),
  );
};
const levelEdited = (actor: RpgmActorView): boolean => {
  const patch = patches.value.get(actorKey(actor, "level"));
  return patch !== undefined;
};
const levelHint = (actor: RpgmActorView): string => {
  if (save.value?.format === "vxace") return "Experience stays unchanged for RPG Maker VX Ace.";
  if (actor.has_curve) return "Experience will be recalculated for the new level. Edit it afterwards to override.";
  if (isRgss.value) return "No experience table was found for this actor, so experience stays unchanged.";
  return "Class data was not found, so experience stays unchanged.";
};

watch(switchFilter, () => (switchLimit.value = ROW_PAGE));
watch(variableFilter, () => (variableLimit.value = ROW_PAGE));
const switchMatches = computed(() => filterRows(view.value?.switches ?? [], switchFilter.value));
const variableMatches = computed(() => filterRows(view.value?.variables ?? [], variableFilter.value));
const switchRows = computed(() => switchMatches.value.slice(0, switchLimit.value));
const variableRows = computed(() => variableMatches.value.slice(0, variableLimit.value));
const switchPath = (id: number): RpgmPath => ["switches", "_data", id];
const variablePath = (id: number): RpgmPath => ["variables", "_data", id];
const switchKey = (row: RpgmValueRow): string => leafKey(row.ref, switchPath(row.id));
const variableKey = (row: RpgmValueRow): string => leafKey(row.ref, variablePath(row.id));
const rowLabel = (kind: string, row: RpgmValueRow): string => (row.name ? `${kind} #${row.id} · ${row.name}` : `${kind} #${row.id}`);
const toggleSwitch = (row: RpgmValueRow, event: Event): void => {
  const value = (event.target as HTMLInputElement).checked;
  record(switchKey(row), { op: "set_switch", id: row.id, value }, row.value as boolean, rowLabel("Switch", row), "switch");
};
const variableFieldId = (row: RpgmValueRow): string => `variable:${row.id}`;
const editVariable = (row: RpgmValueRow, event: Event): void => {
  const text = inputValue(event);
  const key = variableKey(row);
  typed({ id: variableFieldId(row), tab: "variables", key }, text, validateVariable(text, row.type), (value) =>
    record(key, { op: "set_variable", id: row.id, value }, row.value as number | string | null, rowLabel("Variable", row)),
  );
};

const editRaw = (path: RpgmPath, original: RawScalar, text: string): void => {
  const key = pathKey(path);
  typed({ id: rawFieldId(path), tab: "raw", key }, text, parseRawInput(original, text), (value) =>
    record(key, { op: "set", path, value }, original, `Raw · ${rawPathLabel(path)}`),
  );
};

const editRgssRaw = (nodeId: number, type: RgssScalarType, original: RawScalar, text: string): void => {
  const key = refKey(nodeId);
  typed({ id: rgssFieldId(nodeId), tab: "raw", key }, text, parseRgssRawInput(type, text), (value) =>
    record(key, { op: "set_node", node: nodeId, value }, original, `Raw · ${rgssNodeLabel(rgssDumpEntries.value, nodeId)}`),
  );
};

// Footer "Fix N fields on …": open the first tab with an error and focus its
// first invalid field. A filtered variable list is widened to show it.
const firstVisibleInvalid = (): HTMLElement | null =>
  [...(panelRef.value?.querySelectorAll<HTMLElement>('[aria-invalid="true"]') ?? [])].find((el) => el.getClientRects().length > 0) ?? null;
const goToFirstError = async (): Promise<void> => {
  const target = firstInvalidTab(drafts.value, TABS.map((entry) => entry.id));
  if (!target) return;
  tab.value = target;
  await nextTick();
  let field = firstVisibleInvalid();
  if (!field && target === "variables") {
    // Clear the filter; if the row is past the shown page, filter by its id.
    const id = [...drafts.value].find(([, draft]) => draft.tab === "variables" && draft.error !== null)?.[0].split(":")[1];
    for (const filter of id ? ["", id] : [""]) {
      if (field) break;
      variableFilter.value = filter;
      await nextTick();
      field = firstVisibleInvalid();
    }
  }
  if (field) {
    field.focus();
    if (field instanceof HTMLInputElement) field.select();
  } else {
    // A Raw field inside a branch that was closed again: show the tab at least.
    panelRef.value?.focus();
  }
};

// ---- saving ----

const canSave = computed(() => !!save.value && dirty.value && !hasErrors.value && !busy.value && !gameRunning.value);

// Ctrl+S (Cmd+S) saves when the Save button is enabled.
const onEditorKeydown = (event: KeyboardEvent): void => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== "s") return;
  event.preventDefault();
  if (canSave.value) void doSave();
};

const doSave = async (): Promise<void> => {
  if (!canSave.value || !save.value) return;
  saving.value = true;
  saveError.value = "";
  const count = patches.value.size;
  try {
    const result = await api.writeRpgmSave(
      props.gameId,
      props.file,
      buildPatchList(patches.value),
      save.value.size,
      save.value.mtime_ns,
      save.value.revision,
    );
    if (result.success === false) {
      saveError.value = result.error || "The save could not be written";
      return;
    }
    const backup = ("backup" in result && result.backup) || "";
    notify(("message" in result && result.message) || "Saved", "success");
    emit("saved", backup);
    await load();
    // Set after the reload: reloading clears the pending edits, which would hide it.
    lastSaved.value = { backup, count };
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : "The save could not be written";
    notifyError(saveError.value);
  } finally {
    saving.value = false;
  }
};

// Puts back the saves folder from the backup this write made. The backend
// backs up the current saves first, so the undo can itself be undone.
const undoSave = async (): Promise<void> => {
  const backup = lastSaved.value?.backup;
  if (!backup || busy.value || gameRunning.value) return;
  const ok = await confirmAction({
    title: "Undo this save?",
    message: `The saves folder goes back to backup ${backup}, from just before this save. wLib backs up the current saves first.`,
    confirmLabel: "Undo save",
  });
  if (!ok) return;
  restoring.value = true;
  restoreError.value = "";
  try {
    const result = await api.restoreGameSavesBackup(props.gameId, backup);
    if (result.success === false) {
      restoreError.value = result.error || "The backup could not be restored";
      return;
    }
    notify(result.message || "Restored the previous saves", "success");
    lastSaved.value = null;
    emit("saved", result.backup || "");
    await load();
  } catch (error) {
    restoreError.value = error instanceof Error ? error.message : "The backup could not be restored";
  } finally {
    restoring.value = false;
  }
};

const showBackup = async (): Promise<void> => {
  const backup = lastSaved.value?.backup;
  if (!backup) return;
  try {
    const result = await api.openGameSaveBackup(props.gameId, backup);
    if (result.success === false) notifyError(result.error || "Could not open the backup folder");
  } catch (error) {
    notifyError(error instanceof Error ? error.message : "Could not open the backup folder");
  }
};

const slotLabel = computed(() => {
  // Number() drops the leading zero of VX Ace names (Save01 is slot 1).
  const slot = props.file.replace(/^(?:file|save)(\d+)\..*$/i, (_match, digits: string) => `Slot ${Number(digits)}`);
  const fmt = formatLabel(save.value?.format);
  return fmt ? `${slot} (${fmt})` : slot;
});
</script>

<template>
  <Teleport to="body">
  <div v-if="isOpen" class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <!-- Same scrim as the game detail dialog. Over that dialog the two scrims stack, so the editor still reads as on top. -->
    <div class="absolute inset-0 bg-black/85" @click="close"></div>
    <div
      ref="modalRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rpgm-editor-title"
      tabindex="-1"
      class="editor-modal w-full max-w-3xl rounded-2xl relative overflow-hidden flex flex-col h-[min(calc(var(--app-vh)*0.9),52rem)] outline-none"
      @keydown="onEditorKeydown"
    >
      <div class="shrink-0 px-6 py-4 flex items-center justify-between gap-3" style="border-bottom: 1px solid var(--border)">
        <div class="min-w-0">
          <h3 id="rpgm-editor-title" class="text-lg font-bold" style="color: var(--text-primary)">Save editor · {{ slotLabel }}</h3>
          <p class="text-xs mt-0.5" style="color: var(--text-muted)">
            <span role="status"><span v-if="dirty" class="editor-dirty">{{ countChanges(patches.size) }} not saved</span></span>
            <span v-if="dirty" aria-hidden="true"> · </span>
            <span>A backup of the saves folder is made before each save.</span>
          </p>
        </div>
        <button type="button" class="editor-close shrink-0 -my-1 -me-2 rounded-lg" aria-label="Close" title="Close (Esc)" @click="close"><IconX class="w-5 h-5" aria-hidden="true" /></button>
      </div>

      <div v-if="loading" class="flex-1 p-10 flex items-center justify-center gap-2 text-sm" style="color: var(--text-muted)" role="status">
        <IconLoader2 :size="16" class="animate-spin" /> Reading save…
      </div>
      <div v-else-if="loadError" class="flex-1 p-6 flex gap-3">
        <IconAlertCircle class="w-5 h-5 shrink-0" style="color: var(--danger-text)" aria-hidden="true" />
        <div class="min-w-0 space-y-3">
          <p role="alert" class="text-sm" style="color: var(--danger-text)">{{ loadError }}</p>
          <p class="text-xs" style="color: var(--text-muted)">If the save file was moved or deleted, close the editor and choose the save again.</p>
          <div class="flex gap-2">
            <button type="button" class="editor-btn" @click="load">Try again</button>
            <button type="button" class="editor-btn" @click="close">Close</button>
          </div>
        </div>
      </div>

      <template v-else-if="save && view && names">
        <div class="shrink-0 px-6 pt-3 flex gap-1 flex-wrap" role="tablist" aria-label="Save sections">
          <button
            v-for="(entry, index) in TABS"
            :id="`rpgm-editor-tab-${entry.id}`"
            :key="entry.id"
            type="button"
            role="tab"
            :aria-selected="tab === entry.id"
            aria-controls="rpgm-editor-panel"
            :tabindex="tab === entry.id ? 0 : -1"
            class="editor-tab"
            :class="{ 'editor-tab-active': tab === entry.id }"
            @click="tab = entry.id"
            @keydown="onTabKeydown($event, index)"
          >
            {{ entry.label }}
            <template v-if="tabErrors[entry.id]">
              <span class="editor-tab-badge" aria-hidden="true">{{ tabErrors[entry.id] }}</span>
              <span class="sr-only">, {{ tabErrorText(tabErrors[entry.id] ?? 0) }}</span>
            </template>
          </button>
        </div>

        <div
          id="rpgm-editor-panel"
          ref="panelRef"
          class="flex-1 min-h-0 overflow-y-auto px-6 py-4 space-y-4 outline-none"
          role="tabpanel"
          :aria-labelledby="`rpgm-editor-tab-${tab}`"
          tabindex="-1"
        >
          <template v-if="tab === 'party'">
            <p v-if="view.missing.includes('gold') && view.missing.includes('items')" class="text-xs" style="color: var(--text-muted)">Party data was not found in this save. Use the Raw tab.</p>
            <div v-if="view.gold !== null">
              <label :for="fieldDomId('gold')" class="text-xs font-medium" style="color: var(--text-secondary)">Gold{{ names.currency ? ` (${names.currency})` : "" }}</label>
              <input
                :id="fieldDomId('gold')"
                class="editor-input w-full max-w-xs mt-1 block"
                inputmode="numeric"
                :value="fieldText('gold', pendingValue(goldKey, view.gold))"
                v-bind="invalidAttrs('gold')"
                @input="editGold"
                @blur="settle('gold')"
              />
              <p v-if="fieldError('gold')" :id="fieldErrorId('gold')" class="editor-error mt-1">{{ fieldError("gold") }}</p>
            </div>

            <section v-for="kind in (['item', 'weapon', 'armor'] as RpgmInventoryKind[])" :key="kind" :aria-label="KIND_LABEL[kind]">
              <h4 class="text-xs font-semibold uppercase tracking-wide" style="color: var(--text-secondary)">{{ KIND_LABEL[kind] }}</h4>
              <p v-if="inventoryRows(kind).length === 0" class="text-xs mt-1" style="color: var(--text-muted)">None held.</p>
              <ul class="mt-1 space-y-1">
                <li v-for="row in inventoryRows(kind)" :key="row.id" class="editor-row">
                  <span class="flex-1 min-w-0 truncate text-sm" :style="{ color: row.known ? 'var(--text-primary)' : 'var(--text-muted)' }">
                    {{ row.name }} <span v-if="row.known" class="text-xs" style="color: var(--text-muted)">#{{ row.id }}</span>
                  </span>
                  <input
                    class="editor-input w-24 text-right"
                    inputmode="numeric"
                    :value="fieldText(countFieldId(kind, row.id), pendingValue(countKey(kind, row.id, row.ref), row.original ?? 0))"
                    :aria-label="`Count of ${row.name}`"
                    v-bind="invalidAttrs(countFieldId(kind, row.id))"
                    @input="editCount(kind, row.id, row.name, row.original, row.ref, $event)"
                    @blur="settle(countFieldId(kind, row.id))"
                  />
                  <span v-if="fieldError(countFieldId(kind, row.id))" :id="fieldErrorId(countFieldId(kind, row.id))" class="editor-error editor-row-error">
                    {{ fieldError(countFieldId(kind, row.id)) }}
                  </span>
                </li>
              </ul>
            </section>

            <section aria-label="Add to inventory" class="space-y-2">
              <h4 class="text-xs font-semibold uppercase tracking-wide" style="color: var(--text-secondary)">Add</h4>
              <div class="flex gap-2">
                <select v-model="pickerKind" class="editor-input" aria-label="Kind to add">
                  <option value="item">Item</option>
                  <option value="weapon">Weapon</option>
                  <option value="armor">Armor</option>
                </select>
                <input v-model="pickerQuery" class="editor-input flex-1" placeholder="Search by name or id" aria-label="Search the database" />
              </div>
              <ul class="editor-pick-list max-h-40 overflow-y-auto">
                <li v-for="entry in pickerEntries" :key="entry.id">
                  <button type="button" class="editor-pick" :title="entry.description" @click="addEntry(entry.id, entry.name)">
                    <span class="truncate">{{ entry.name }}</span>
                    <span v-if="badgeForKind(entry.kind)" class="editor-badge">{{ badgeForKind(entry.kind) }}</span>
                    <span class="text-xs" style="color: var(--text-muted)">#{{ entry.id }}</span>
                    <IconPlus class="editor-pick-add w-4 h-4 ml-auto shrink-0" aria-hidden="true" />
                  </button>
                </li>
                <li v-if="pickerEntries.length === 0" class="text-xs px-3 py-2" style="color: var(--text-muted)">Nothing matches.</li>
              </ul>
            </section>
          </template>

          <template v-else-if="tab === 'actors'">
            <p v-if="view.actors.length === 0" class="text-xs" style="color: var(--text-muted)">Actors were not found in this save.</p>
            <section v-for="actor in view.actors" :key="actor.id" class="editor-card space-y-2" :aria-label="`Actor ${actor.id}`">
              <h4 class="text-sm font-semibold" style="color: var(--text-primary)">
                {{ pendingValue(actorKey(actor, 'name'), actor.name) }}
                <span class="text-xs font-normal" style="color: var(--text-muted)">#{{ actor.id }}{{ actor.class_name ? ` · ${actor.class_name}` : "" }}</span>
              </h4>
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div v-for="entry in actorFields(actor)" :key="entry.field" class="editor-field">
                  <label :for="fieldDomId(actorFieldId(actor, entry.field))">{{ fieldLabelOf(entry.field) }}</label>
                  <input
                    :id="fieldDomId(actorFieldId(actor, entry.field))"
                    class="editor-input"
                    :inputmode="entry.numeric ? 'numeric' : undefined"
                    :disabled="entry.disabled"
                    :value="fieldText(actorFieldId(actor, entry.field), pendingValue(actorKey(actor, entry.field), entry.shown))"
                    v-bind="invalidAttrs(actorFieldId(actor, entry.field))"
                    @input="editActor(actor, entry.field, entry.original, $event)"
                    @blur="settle(actorFieldId(actor, entry.field))"
                  />
                  <p v-if="fieldError(actorFieldId(actor, entry.field))" :id="fieldErrorId(actorFieldId(actor, entry.field))" class="editor-error">
                    {{ fieldError(actorFieldId(actor, entry.field)) }}
                  </p>
                </div>
              </div>
              <p v-if="levelEdited(actor)" class="text-xs" style="color: var(--text-muted)" role="status">
                {{ levelHint(actor) }}
              </p>
              <p class="text-xs font-medium" style="color: var(--text-secondary)">Parameter bonuses</p>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div v-for="(label, index) in names.params" :key="index" class="editor-field">
                  <label :for="fieldDomId(bonusFieldId(actor, index))">{{ label }}</label>
                  <input
                    :id="fieldDomId(bonusFieldId(actor, index))"
                    class="editor-input"
                    inputmode="numeric"
                    :value="fieldText(bonusFieldId(actor, index), pendingValue(bonusKey(actor, index), actor.param_plus[index] ?? 0))"
                    v-bind="invalidAttrs(bonusFieldId(actor, index))"
                    @input="editBonus(actor, index, actor.param_plus[index] ?? 0, $event)"
                    @blur="settle(bonusFieldId(actor, index))"
                  />
                  <p v-if="fieldError(bonusFieldId(actor, index))" :id="fieldErrorId(bonusFieldId(actor, index))" class="editor-error">
                    {{ fieldError(bonusFieldId(actor, index)) }}
                  </p>
                </div>
              </div>
            </section>
          </template>

          <template v-else-if="tab === 'switches'">
            <p v-if="view.missing.includes('switches')" class="text-xs" style="color: var(--text-muted)">Switches were not found in this save.</p>
            <input v-model="switchFilter" class="editor-input w-full" placeholder="Filter by id or name" aria-label="Filter switches" />
            <ul class="space-y-1">
              <li v-for="row in switchRows" :key="row.id" class="editor-row">
                <label class="flex items-center gap-3 flex-1 min-w-0 cursor-pointer">
                  <input
                    type="checkbox"
                    :checked="pendingValue(switchKey(row), row.value as boolean)"
                    :aria-label="`Switch ${row.id} ${row.name}`"
                    @change="toggleSwitch(row, $event)"
                  />
                  <span class="text-sm truncate" style="color: var(--text-primary)">{{ row.name || "(unnamed)" }}</span>
                  <span class="text-xs" style="color: var(--text-muted)">#{{ row.id }}</span>
                </label>
              </li>
              <li v-if="switchRows.length === 0" class="text-xs" style="color: var(--text-muted)">No switches match.</li>
              <li v-if="switchMatches.length > switchRows.length">
                <button type="button" class="editor-link" @click="switchLimit += ROW_PAGE">Show more ({{ switchMatches.length - switchRows.length }} left)</button>
              </li>
            </ul>
          </template>

          <template v-else-if="tab === 'variables'">
            <p v-if="view.missing.includes('variables')" class="text-xs" style="color: var(--text-muted)">Variables were not found in this save.</p>
            <input v-model="variableFilter" class="editor-input w-full" placeholder="Filter by id or name" aria-label="Filter variables" />
            <ul class="space-y-1">
              <li v-for="row in variableRows" :key="row.id" class="editor-row">
                <span class="flex-1 min-w-0 truncate text-sm" style="color: var(--text-primary)">
                  {{ row.name || "(unnamed)" }} <span class="text-xs" style="color: var(--text-muted)">#{{ row.id }}</span>
                </span>
                <input
                  v-if="row.type === 'number' || row.type === 'string'"
                  class="editor-input w-40"
                  :value="fieldText(variableFieldId(row), pendingValue(variableKey(row), row.value as number | string))"
                  :aria-label="`Variable ${row.id} ${row.name}`"
                  v-bind="invalidAttrs(variableFieldId(row))"
                  @input="editVariable(row, $event)"
                  @blur="settle(variableFieldId(row))"
                />
                <span v-else class="text-xs" style="color: var(--text-muted)" title="Holds a value the editor cannot change">read-only</span>
                <span v-if="fieldError(variableFieldId(row))" :id="fieldErrorId(variableFieldId(row))" class="editor-error editor-row-error">{{ fieldError(variableFieldId(row)) }}</span>
              </li>
              <li v-if="variableRows.length === 0" class="text-xs" style="color: var(--text-muted)">No variables match.</li>
              <li v-if="variableMatches.length > variableRows.length">
                <button type="button" class="editor-link" @click="variableLimit += ROW_PAGE">Show more ({{ variableMatches.length - variableRows.length }} left)</button>
              </li>
            </ul>
          </template>

          <!-- Kept mounted so open branches (and a field with an error in them) stay when the tab changes -->
          <div v-show="tab === 'raw'" class="space-y-4">
            <template v-if="isRgss">
              <p class="text-xs" style="color: var(--text-muted)">Change any existing UTF-8 text, number or true/false value. Links, symbols, hash keys and opaque engine data are read-only.</p>
              <RgssRawNode :entries="rgssDumpEntries" :patches="patches" :drafts="drafts" @draft="editRgssRaw" @settle="settle" />
            </template>
            <template v-else>
              <p class="text-xs" style="color: var(--text-muted)">Change any existing text, number or true/false value. Keys starting with @ are used by RPG Maker and are read-only.</p>
              <RpgmRawNode :node="save.raw" :path="[]" :patches="patches" :drafts="drafts" @draft="editRaw" @settle="settle" />
            </template>
          </div>
        </div>

        <div class="editor-footer shrink-0">
          <div v-if="dirty && reviewOpen" id="rpgm-editor-changes" class="editor-changes">
            <ul class="editor-change-list" aria-label="Changes to save">
              <li v-for="change in changes" :key="change.key" class="editor-change">
                <span class="editor-change-label" :title="change.label">{{ change.label }}</span>
                <span class="editor-change-values">
                  <span class="editor-change-before" :title="change.before">{{ change.before }}</span>
                  <span aria-hidden="true" class="editor-change-arrow">→</span>
                  <span class="sr-only">to</span>
                  <span class="editor-change-after" :title="change.after">{{ change.after }}</span>
                </span>
              </li>
            </ul>
          </div>

          <div v-if="lastSaved && !dirty" class="editor-saved" role="status">
            <IconCircleCheck class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div class="editor-saved-text min-w-0">
              <p class="text-xs font-semibold">Saved {{ countChanges(lastSaved.count) }}.</p>
              <p v-if="lastSaved.backup" class="text-xs editor-saved-detail">
                The saves from before are in backup <span class="editor-backup-name">{{ lastSaved.backup }}</span>.
              </p>
              <p v-if="restoreError" role="alert" class="text-xs mt-1" style="color: var(--danger-text)">{{ restoreError }}</p>
            </div>
            <div v-if="lastSaved.backup" class="flex gap-2 shrink-0 flex-wrap">
              <button type="button" class="editor-btn editor-btn-sm" :disabled="busy || gameRunning" :title="gameRunning ? 'Close the game first' : undefined" @click="undoSave">
                <IconLoader2 v-if="restoring" :size="12" class="animate-spin mr-1" aria-hidden="true" />{{ restoring ? "Undoing…" : "Undo save" }}
              </button>
              <button type="button" class="editor-btn editor-btn-sm" @click="showBackup">Show backup</button>
            </div>
          </div>

          <div class="px-6 py-3 flex items-center gap-3 flex-wrap">
            <div class="editor-footer-info min-w-0 space-y-1">
              <p v-if="saveError" role="alert" class="text-xs" style="color: var(--danger-text)">
                {{ saveError }}
                <button v-if="saveError.includes('Reopen')" type="button" class="editor-link" @click="load">Reopen</button>
              </p>
              <button
                v-if="dirty"
                type="button"
                class="editor-review"
                :aria-expanded="reviewOpen"
                aria-controls="rpgm-editor-changes"
                @click="reviewOpen = !reviewOpen"
              >
                <IconChevronDown class="w-3.5 h-3.5 editor-review-icon" :class="{ 'editor-review-icon-open': reviewOpen }" aria-hidden="true" />
                {{ reviewOpen ? "Hide" : "Review" }} {{ countChanges(patches.size) }}
              </button>
              <p v-if="hasErrors" role="status" class="text-xs">
                <button id="rpgm-editor-fix" type="button" class="editor-fix" @click="goToFirstError">
                  <IconAlertCircle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />{{ fixLabel }}
                </button>
              </p>
              <p id="rpgm-editor-save-rule" class="text-xs" :class="gameRunning ? 'editor-running' : ''" :style="gameRunning ? undefined : 'color: var(--text-muted)'">
                {{ gameRunning ? "The game is running. Close it to save." : "Saving needs the game closed." }}
              </p>
            </div>
            <div class="flex gap-2 ml-auto">
              <button type="button" class="editor-btn" :disabled="(!dirty && !hasErrors) || busy" @click="discard">Discard</button>
              <button
                type="button"
                class="editor-btn editor-primary ui-btn-solid"
                :disabled="!dirty || hasErrors || busy || gameRunning"
                :aria-describedby="hasErrors ? 'rpgm-editor-fix rpgm-editor-save-rule' : 'rpgm-editor-save-rule'"
                aria-keyshortcuts="Control+S"
                title="Save (Ctrl+S)"
                @click="doSave"
              >
                <IconLoader2 v-if="saving" :size="14" class="animate-spin mr-1" aria-hidden="true" />{{ saving ? "Saving…" : saveButtonLabel(patches.size) }}
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
  </Teleport>
</template>

<style scoped>
.editor-modal { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.editor-input { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.375rem 0.75rem; color: var(--text-primary); font-size: 0.875rem; }
.editor-input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
.editor-input:disabled { opacity: 0.5; }
.editor-btn { display: inline-flex; align-items: center; justify-content: center; background: var(--bg-overlay); border: 1px solid var(--border-hover); color: var(--text-primary); padding: 0.375rem 0.875rem; border-radius: 0.5rem; font-size: 0.875rem; font-weight: 500; }
.editor-btn:hover:not(:disabled) { background: var(--border-hover); }
.editor-btn:disabled { opacity: 0.5; }
.editor-primary { background: var(--brand); border-color: var(--brand); color: var(--on-brand); }
.editor-primary:hover:not(:disabled) { background: var(--brand); filter: brightness(1.1); }
.editor-tab { position: relative; padding: 0.375rem 0.875rem; border-radius: 0.5rem 0.5rem 0 0; font-size: 0.8125rem; color: var(--text-secondary); background: transparent; border: 0; border-bottom: 2px solid transparent; }
.editor-tab-active { color: var(--text-primary); border-bottom-color: var(--brand); }
.editor-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.editor-card { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.75rem; padding: 0.75rem; }
.editor-field { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.75rem; color: var(--text-secondary); }
.editor-error { color: var(--danger-text); font-size: 0.75rem; overflow-wrap: anywhere; }
.editor-row-error { flex-basis: 100%; text-align: end; }
.editor-input[aria-invalid="true"] { border-color: var(--danger-text); }
.editor-input[aria-invalid="true"]:focus { box-shadow: 0 0 0 3px var(--danger-bg); }
/* Sits over the tab's top-right corner, outside the label, so a badge that comes or goes never changes the tab width. */
.editor-tab-badge { position: absolute; top: -0.125rem; inset-inline-end: 0; transform: translateX(50%); display: inline-flex; align-items: center; justify-content: center; min-width: 1.125rem; height: 1.125rem; padding: 0 0.3rem; border-radius: 9999px; background: var(--danger-bg); border: 1px solid var(--danger-border); color: var(--danger-text); font-size: 0.6875rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; vertical-align: 0.0625rem; }
.editor-fix { display: inline-flex; align-items: center; gap: 0.25rem; background: none; border: 0; padding: 0; font-size: 0.75rem; font-weight: 600; color: var(--danger-text); border-radius: 0.25rem; cursor: pointer; text-align: start; }
.editor-fix:hover { text-decoration: underline; }
.editor-fix:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.editor-dirty { color: var(--brand); font-weight: 600; }
.editor-pick-list { border: 1px solid var(--border); border-radius: 0.5rem; background: var(--bg-inset); }
.editor-pick-list > li + li { border-top: 1px solid var(--border); }
.editor-pick { display: flex; align-items: center; gap: 0.5rem; width: 100%; text-align: left; background: transparent; padding: 0.4rem 0.75rem; font-size: 0.8125rem; color: var(--text-primary); cursor: pointer; }
.editor-pick:hover { background: var(--bg-overlay); }
.editor-pick:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; }
.editor-pick-add { color: var(--text-muted); }
.editor-pick:hover .editor-pick-add { color: var(--brand); }
/* 36px hit area; the negative margins keep the header height. */
.editor-close { display: inline-flex; align-items: center; justify-content: center; width: 2.25rem; height: 2.25rem; color: var(--text-muted); }
.editor-close:hover { color: var(--text-primary); background: var(--surface-hover); }
.editor-close:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.editor-badge { font-size: 0.6875rem; padding: 0 0.375rem; border-radius: 9999px; background: var(--bg-overlay); color: var(--text-secondary); border: 1px solid var(--border); }
.editor-link { color: var(--brand); background: none; border: 0; text-decoration: underline; font-size: 0.75rem; }
.editor-btn-sm { padding: 0.25rem 0.625rem; font-size: 0.75rem; }
.editor-btn:focus-visible, .editor-review:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.editor-footer { border-top: 1px solid var(--border); }
.editor-footer-info { flex: 1 1 14rem; }
.editor-changes { border-bottom: 1px solid var(--border); background: var(--bg-inset); }
.editor-change-list { max-height: 10rem; overflow-y: auto; padding: 0.5rem 1.5rem; }
.editor-change { display: flex; align-items: baseline; gap: 0.75rem; padding: 0.2rem 0; font-size: 0.75rem; }
.editor-change + .editor-change { border-top: 1px solid var(--border); }
.editor-change-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
.editor-change-values { display: flex; align-items: baseline; gap: 0.375rem; flex: 0 1 auto; min-width: 0; max-width: 55%; font-variant-numeric: tabular-nums; }
.editor-change-before, .editor-change-after { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.editor-change-before { color: var(--text-muted); }
.editor-change-after { color: var(--text-primary); font-weight: 600; }
.editor-change-arrow { color: var(--text-muted); flex-shrink: 0; }
.editor-review { display: inline-flex; align-items: center; gap: 0.25rem; background: none; border: 0; padding: 0; font-size: 0.75rem; font-weight: 600; color: var(--brand); border-radius: 0.25rem; cursor: pointer; }
.editor-review:hover { text-decoration: underline; }
.editor-review-icon { transition: transform 0.15s ease-out; }
.editor-review-icon-open { transform: rotate(180deg); }
.editor-running { color: var(--warning-text); font-weight: 600; }
.editor-saved { display: flex; align-items: flex-start; gap: 0.625rem; flex-wrap: wrap; margin: 0.75rem 1.5rem 0; padding: 0.625rem 0.75rem; border-radius: 0.5rem; background: var(--success-bg); border: 1px solid var(--success-border); color: var(--success-text); }
.editor-saved-text { flex: 1 1 12rem; }
.editor-saved-detail { color: var(--text-secondary); }
.editor-backup-name { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--text-primary); overflow-wrap: anywhere; }
@media (prefers-reduced-motion: reduce) { .editor-review-icon { transition: none; } }
</style>
