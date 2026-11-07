<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { IconLoader2, IconX } from "@tabler/icons-vue";
import { api, type RpgmInventoryKind, type RpgmPatch, type RpgmSaveLoadResponse } from "../../services/api";
import { confirmAction } from "../../utils/confirm";
import { notify, notifyError } from "../../utils/toast";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import RpgmRawNode from "../RpgmRawNode.vue";
import {
  badgeForKind,
  buildPatchList,
  clearPatch,
  filterPicker,
  filterRows,
  isDirty,
  patchValue,
  pathKey,
  setPatch,
  validateBonus,
  validateCount,
  validateExp,
  validateGold,
  validateLevel,
  validateName,
  validateStat,
  validateVariable,
  type PatchMap,
  type RawScalar,
  type RpgmPath,
  type Validated,
} from "../../utils/rpgmSaveEditor";

const props = defineProps<{ modelValue: boolean; gameId: number; file: string }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; saved: [backup: string] }>();

type Tab = "party" | "actors" | "switches" | "variables" | "raw";
const TABS: { id: Tab; label: string }[] = [
  { id: "party", label: "Party" },
  { id: "actors", label: "Actors" },
  { id: "switches", label: "Switches" },
  { id: "variables", label: "Variables" },
  { id: "raw", label: "Raw" },
];
const KIND_KEY: Record<RpgmInventoryKind, string> = { item: "_items", weapon: "_weapons", armor: "_armors" };
const KIND_LABEL: Record<RpgmInventoryKind, string> = { item: "Items", weapon: "Weapons", armor: "Armors" };

const modalRef = ref<HTMLElement | null>(null);
const loading = ref(false);
const loadError = ref("");
const saveError = ref("");
const saving = ref(false);
const save = ref<RpgmSaveLoadResponse | null>(null);
const patches = ref<PatchMap>(new Map());
const errors = ref<Record<string, string>>({});
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
const hasErrors = computed(() => Object.keys(errors.value).length > 0);
const view = computed(() => save.value?.view ?? null);
const names = computed(() => save.value?.names ?? null);

const reset = (): void => {
  patches.value = new Map();
  errors.value = {};
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
  () => [props.modelValue, props.file] as const,
  ([open]) => {
    if (open) {
      tab.value = "party";
      switchLimit.value = ROW_PAGE;
      variableLimit.value = ROW_PAGE;
      switchFilter.value = "";
      variableFilter.value = "";
      pickerQuery.value = "";
      void load();
    } else {
      loadToken += 1;
    }
  },
  { immediate: true },
);

const close = async (): Promise<void> => {
  if (saving.value) return;
  if (dirty.value) {
    const ok = await confirmAction({
      title: "Discard changes?",
      message: "This save has unsaved changes. Close the editor and lose them?",
      confirmLabel: "Discard",
      danger: true,
    });
    if (!ok) return;
  }
  emit("update:modelValue", false);
};

useModalKeyboard(modalRef, () => props.modelValue, () => void close());

const discard = (): void => {
  reset();
};

// ---- editing ----

const fail = (id: string, result: Validated<unknown>): boolean => {
  if (result.ok) {
    const { [id]: _removed, ...rest } = errors.value;
    errors.value = rest;
    return false;
  }
  errors.value = { ...errors.value, [id]: result.error };
  return true;
};

// Store the patch, or drop it when the value is back to what the save held.
const record = (key: string, patch: RpgmPatch, original: unknown): void => {
  patches.value = patchValue(patch) === original ? clearPatch(patches.value, key) : setPatch(patches.value, key, patch);
};

const inputValue = (event: Event): string => (event.target as HTMLInputElement).value;

const pending = (key: string): RpgmPatch | undefined => patches.value.get(key);
const pendingValue = <T,>(key: string, original: T): T => {
  const patch = pending(key);
  return patch ? (patchValue(patch) as T) : original;
};

const GOLD_PATH: RpgmPath = ["party", "_gold"];
const editGold = (event: Event): void => {
  const result = validateGold(inputValue(event));
  if (fail("gold", result) || !result.ok) return;
  record(pathKey(GOLD_PATH), { op: "set", path: GOLD_PATH, value: result.value }, view.value?.gold);
};

const countPath = (kind: RpgmInventoryKind, id: number): RpgmPath => ["party", KIND_KEY[kind], String(id)];
const inventoryRows = (kind: RpgmInventoryKind) => {
  const rows = (view.value?.inventory[kind] ?? []).map((row) => ({ ...row, original: row.count as number | null }));
  for (const extra of added.value) {
    if (extra.kind === kind && !rows.some((row) => row.id === extra.id)) {
      rows.push({ id: extra.id, count: 0, name: extra.name, known: true, original: null });
    }
  }
  return rows.sort((a, b) => a.id - b.id);
};
const editCount = (kind: RpgmInventoryKind, id: number, original: number | null, event: Event): void => {
  const result = validateCount(inputValue(event));
  if (fail(`count:${kind}:${id}`, result) || !result.ok) return;
  record(pathKey(countPath(kind, id)), { op: "set_count", kind, id, count: result.value }, original ?? 0);
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
  patches.value = setPatch(patches.value, pathKey(countPath(kind, id)), { op: "set_count", kind, id, count: 1 });
};

const actorPath = (id: number, leaf: string | number, extra?: string | number): RpgmPath =>
  extra === undefined ? ["actors", "_data", id, leaf] : ["actors", "_data", id, leaf, extra];

type ActorField = "name" | "level" | "exp" | "hp" | "mp";
const LEAF: Record<ActorField, string> = { name: "_name", level: "_level", exp: "_exp", hp: "_hp", mp: "_mp" };
const actorKey = (id: number, field: ActorField, classId: number | null): string =>
  pathKey(field === "exp" ? actorPath(id, "_exp", String(classId)) : actorPath(id, LEAF[field]));
const editActor = (id: number, field: ActorField, original: unknown, classId: number | null, event: Event): void => {
  const text = inputValue(event);
  const validators = { name: validateName, level: validateLevel, exp: validateExp, hp: validateStat, mp: validateStat } as const;
  const result = validators[field](text);
  if (fail(`actor:${id}:${field}`, result) || !result.ok) return;
  record(actorKey(id, field, classId), { op: "set_actor", id, field, value: result.value }, original);
};
const editBonus = (id: number, index: number, original: number, event: Event): void => {
  const result = validateBonus(inputValue(event));
  if (fail(`actor:${id}:bonus:${index}`, result) || !result.ok) return;
  record(pathKey(actorPath(id, "_paramPlus", index)), { op: "set_actor", id, field: "paramPlus", index, value: result.value }, original);
};
const levelEdited = (id: number): boolean => {
  const patch = patches.value.get(pathKey(actorPath(id, "_level")));
  return patch !== undefined;
};

watch(switchFilter, () => (switchLimit.value = ROW_PAGE));
watch(variableFilter, () => (variableLimit.value = ROW_PAGE));
const switchMatches = computed(() => filterRows(view.value?.switches ?? [], switchFilter.value));
const variableMatches = computed(() => filterRows(view.value?.variables ?? [], variableFilter.value));
const switchRows = computed(() => switchMatches.value.slice(0, switchLimit.value));
const variableRows = computed(() => variableMatches.value.slice(0, variableLimit.value));
const switchPath = (id: number): RpgmPath => ["switches", "_data", id];
const variablePath = (id: number): RpgmPath => ["variables", "_data", id];
const toggleSwitch = (id: number, original: boolean, event: Event): void => {
  const value = (event.target as HTMLInputElement).checked;
  record(pathKey(switchPath(id)), { op: "set_switch", id, value }, original);
};
const editVariable = (id: number, original: number | string | null, type: "number" | "string" | "bool" | "other", event: Event): void => {
  const result = validateVariable(inputValue(event), type);
  if (fail(`variable:${id}`, result) || !result.ok) return;
  record(pathKey(variablePath(id)), { op: "set_variable", id, value: result.value }, original);
};

const editRaw = (path: RpgmPath, original: RawScalar, value: RawScalar): void => {
  const key = pathKey(path);
  if (value === original) patches.value = clearPatch(patches.value, key);
  else patches.value = setPatch(patches.value, key, { op: "set", path, value });
};

// ---- saving ----

const doSave = async (): Promise<void> => {
  if (!save.value || saving.value || !dirty.value || hasErrors.value) return;
  saving.value = true;
  saveError.value = "";
  try {
    const result = await api.writeRpgmSave(props.gameId, props.file, buildPatchList(patches.value), save.value.size, save.value.mtime_ns);
    if (result.success === false) {
      saveError.value = result.error || "The save could not be written";
      return;
    }
    notify(("message" in result && result.message) || "Saved");
    emit("saved", ("backup" in result && result.backup) || "");
    await load();
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : "The save could not be written";
    notifyError(saveError.value);
  } finally {
    saving.value = false;
  }
};

const slotLabel = computed(() => props.file.replace(/^file(\d+)\..*$/, "Slot $1"));
</script>

<template>
  <Teleport to="body">
  <div v-if="modelValue" class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
    <div
      ref="modalRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rpgm-editor-title"
      tabindex="-1"
      class="editor-modal w-full max-w-3xl rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none"
    >
      <div class="px-6 py-4 flex items-center justify-between gap-3" style="border-bottom: 1px solid var(--border)">
        <div class="min-w-0">
          <h3 id="rpgm-editor-title" class="text-lg font-bold" style="color: var(--text-primary)">Save editor · {{ slotLabel }}</h3>
          <p class="text-xs mt-0.5" style="color: var(--text-muted)">
            <span v-if="dirty" class="editor-dirty" role="status">Unsaved changes ({{ patches.size }})</span>
            <span v-else>A backup of the saves folder is made before each save.</span>
          </p>
        </div>
        <button type="button" class="editor-btn" aria-label="Close" @click="close"><IconX :size="16" /></button>
      </div>

      <div v-if="loading" class="p-10 flex items-center justify-center gap-2 text-sm" style="color: var(--text-muted)" role="status">
        <IconLoader2 :size="16" class="animate-spin" /> Reading save…
      </div>
      <div v-else-if="loadError" class="p-6 space-y-3">
        <p role="alert" class="text-sm" style="color: var(--danger-text)">{{ loadError }}</p>
        <button type="button" class="editor-btn" @click="load">Try again</button>
      </div>

      <template v-else-if="save && view && names">
        <div class="px-6 pt-3 flex gap-1 flex-wrap" role="tablist" aria-label="Save sections">
          <button
            v-for="entry in TABS"
            :key="entry.id"
            type="button"
            role="tab"
            :aria-selected="tab === entry.id"
            class="editor-tab"
            :class="{ 'editor-tab-active': tab === entry.id }"
            @click="tab = entry.id"
          >
            {{ entry.label }}
          </button>
        </div>

        <div class="flex-1 overflow-y-auto px-6 py-4 space-y-4" role="tabpanel">
          <template v-if="tab === 'party'">
            <p v-if="view.missing.includes('gold') && view.missing.includes('items')" class="text-xs" style="color: var(--text-muted)">Party data was not found in this save. Use the Raw tab.</p>
            <label v-if="view.gold !== null" class="block">
              <span class="text-xs font-medium" style="color: var(--text-secondary)">Gold{{ names.currency ? ` (${names.currency})` : "" }}</span>
              <input
                class="editor-input w-full mt-1"
                inputmode="numeric"
                :value="pendingValue(pathKey(GOLD_PATH), view.gold)"
                aria-label="Gold"
                @change="editGold"
              />
              <span v-if="errors.gold" role="alert" class="editor-error">{{ errors.gold }}</span>
            </label>

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
                    :value="pendingValue(pathKey(countPath(kind, row.id)), row.original ?? 0)"
                    :aria-label="`Count of ${row.name}`"
                    @change="editCount(kind, row.id, row.original, $event)"
                  />
                  <span v-if="errors[`count:${kind}:${row.id}`]" role="alert" class="editor-error">{{ errors[`count:${kind}:${row.id}`] }}</span>
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
              <ul class="space-y-1 max-h-40 overflow-y-auto">
                <li v-for="entry in pickerEntries" :key="entry.id">
                  <button type="button" class="editor-pick" :title="entry.description" @click="addEntry(entry.id, entry.name)">
                    <span class="truncate">{{ entry.name }}</span>
                    <span v-if="badgeForKind(entry.kind)" class="editor-badge">{{ badgeForKind(entry.kind) }}</span>
                    <span class="text-xs" style="color: var(--text-muted)">#{{ entry.id }}</span>
                  </button>
                </li>
                <li v-if="pickerEntries.length === 0" class="text-xs" style="color: var(--text-muted)">Nothing matches.</li>
              </ul>
            </section>
          </template>

          <template v-else-if="tab === 'actors'">
            <p v-if="view.actors.length === 0" class="text-xs" style="color: var(--text-muted)">Actors were not found in this save.</p>
            <section v-for="actor in view.actors" :key="actor.id" class="editor-card space-y-2" :aria-label="`Actor ${actor.id}`">
              <h4 class="text-sm font-semibold" style="color: var(--text-primary)">
                {{ pendingValue(actorKey(actor.id, 'name', actor.class_id), actor.name) }}
                <span class="text-xs font-normal" style="color: var(--text-muted)">#{{ actor.id }}{{ actor.class_name ? ` · ${actor.class_name}` : "" }}</span>
              </h4>
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <label class="editor-field">Name
                  <input class="editor-input" :value="pendingValue(actorKey(actor.id, 'name', actor.class_id), actor.name)" @change="editActor(actor.id, 'name', actor.name, actor.class_id, $event)" />
                </label>
                <label class="editor-field">Level
                  <input class="editor-input" inputmode="numeric" :value="pendingValue(actorKey(actor.id, 'level', actor.class_id), actor.level ?? 1)" @change="editActor(actor.id, 'level', actor.level, actor.class_id, $event)" />
                </label>
                <label class="editor-field">Experience
                  <input
                    class="editor-input"
                    inputmode="numeric"
                    :disabled="actor.exp === null"
                    :value="pendingValue(actorKey(actor.id, 'exp', actor.class_id), actor.exp ?? 0)"
                    @change="editActor(actor.id, 'exp', actor.exp, actor.class_id, $event)"
                  />
                </label>
                <label class="editor-field">HP
                  <input class="editor-input" inputmode="numeric" :value="pendingValue(actorKey(actor.id, 'hp', actor.class_id), actor.hp ?? 0)" @change="editActor(actor.id, 'hp', actor.hp, actor.class_id, $event)" />
                </label>
                <label class="editor-field">MP
                  <input class="editor-input" inputmode="numeric" :value="pendingValue(actorKey(actor.id, 'mp', actor.class_id), actor.mp ?? 0)" @change="editActor(actor.id, 'mp', actor.mp, actor.class_id, $event)" />
                </label>
              </div>
              <p v-if="levelEdited(actor.id)" class="text-xs" style="color: var(--text-muted)" role="status">
                {{ actor.has_curve ? "Experience will be recalculated for the new level. Edit it afterwards to override." : "Class data was not found, so experience stays unchanged." }}
              </p>
              <p class="text-xs font-medium" style="color: var(--text-secondary)">Parameter bonuses</p>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <label v-for="(label, index) in names.params" :key="index" class="editor-field">{{ label }}
                  <input
                    class="editor-input"
                    inputmode="numeric"
                    :value="pendingValue(pathKey(actorPath(actor.id, '_paramPlus', index)), actor.param_plus[index] ?? 0)"
                    @change="editBonus(actor.id, index, actor.param_plus[index] ?? 0, $event)"
                  />
                </label>
              </div>
              <template v-for="(message, id) in errors" :key="id">
                <p v-if="String(id).startsWith(`actor:${actor.id}:`)" role="alert" class="editor-error">{{ message }}</p>
              </template>
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
                    :checked="pendingValue(pathKey(switchPath(row.id)), row.value as boolean)"
                    :aria-label="`Switch ${row.id} ${row.name}`"
                    @change="toggleSwitch(row.id, row.value as boolean, $event)"
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
                  :value="pendingValue(pathKey(variablePath(row.id)), row.value as number | string)"
                  :aria-label="`Variable ${row.id} ${row.name}`"
                  @change="editVariable(row.id, row.value as number | string | null, row.type, $event)"
                />
                <span v-else class="text-xs" style="color: var(--text-muted)" title="Holds a value the editor cannot change">read-only</span>
                <span v-if="errors[`variable:${row.id}`]" role="alert" class="editor-error">{{ errors[`variable:${row.id}`] }}</span>
              </li>
              <li v-if="variableRows.length === 0" class="text-xs" style="color: var(--text-muted)">No variables match.</li>
              <li v-if="variableMatches.length > variableRows.length">
                <button type="button" class="editor-link" @click="variableLimit += ROW_PAGE">Show more ({{ variableMatches.length - variableRows.length }} left)</button>
              </li>
            </ul>
          </template>

          <template v-else>
            <p class="text-xs" style="color: var(--text-muted)">Change any existing text, number or true/false value. Keys starting with @ are used by RPG Maker and are read-only.</p>
            <RpgmRawNode :node="save.raw" :path="[]" :patches="patches" @edit="editRaw" />
          </template>
        </div>

        <div class="px-6 py-3 flex items-center gap-3 flex-wrap" style="border-top: 1px solid var(--border)">
          <p v-if="saveError" role="alert" class="text-xs flex-1 min-w-0" style="color: var(--danger-text)">
            {{ saveError }}
            <button v-if="saveError.includes('Reopen')" type="button" class="editor-link" @click="load">Reopen</button>
          </p>
          <span v-else class="flex-1"></span>
          <button type="button" class="editor-btn" :disabled="!dirty || saving" @click="discard">Discard</button>
          <button type="button" class="editor-btn editor-primary" :disabled="!dirty || hasErrors || saving" @click="doSave">
            <IconLoader2 v-if="saving" :size="14" class="animate-spin mr-1" /> Save
          </button>
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
.editor-primary { background: var(--brand); border-color: var(--brand); color: var(--brand-contrast, #fff); }
.editor-primary:hover:not(:disabled) { background: var(--brand); filter: brightness(1.1); }
.editor-tab { padding: 0.375rem 0.875rem; border-radius: 0.5rem 0.5rem 0 0; font-size: 0.8125rem; color: var(--text-secondary); background: transparent; border: 0; border-bottom: 2px solid transparent; }
.editor-tab-active { color: var(--text-primary); border-bottom-color: var(--brand); }
.editor-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.editor-card { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.75rem; padding: 0.75rem; }
.editor-field { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.75rem; color: var(--text-secondary); }
.editor-error { color: var(--danger-text); font-size: 0.75rem; }
.editor-dirty { color: var(--brand); font-weight: 600; }
.editor-pick { display: flex; align-items: center; gap: 0.5rem; width: 100%; text-align: left; background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.25rem 0.75rem; font-size: 0.8125rem; color: var(--text-primary); }
.editor-pick:hover { border-color: var(--brand); }
.editor-badge { font-size: 0.625rem; padding: 0 0.375rem; border-radius: 9999px; background: var(--bg-overlay); color: var(--text-secondary); border: 1px solid var(--border); }
.editor-link { color: var(--brand); background: none; border: 0; text-decoration: underline; font-size: 0.75rem; }
</style>
