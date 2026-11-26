<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref } from "vue";
import type { RgssRawNodeData, RpgmPatch } from "../services/api";
import {
  draftError,
  draftText,
  fieldErrorId,
  patchValue,
  refKey,
  rgssFieldId,
  type DraftMap,
  type PatchMap,
  type RawScalar,
  type RgssScalarType,
} from "../utils/rpgmSaveEditor";

defineOptions({ name: "RgssRawNode" });

export interface RgssRawEntry {
  id: string;
  label: string;
  node: RgssRawNodeData;
}

const props = defineProps<{
  entries: RgssRawEntry[];
  patches: PatchMap;
  // Typed text and errors, owned by the editor so an error blocks Save
  drafts: DraftMap;
}>();
// The editor checks the text against the node's Marshal type.
const emit = defineEmits<{ draft: [nodeId: number, type: RgssScalarType, original: RawScalar, text: string]; settle: [id: string] }>();

const MAX_ROWS = 200;
const open = ref<Set<string>>(new Set());
const shown = ref(MAX_ROWS);

const toggle = (id: string): void => {
  const next = new Set(open.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  open.value = next;
};

const isContainer = (node: RgssRawNodeData): boolean =>
  node.t === "object" ||
  node.t === "struct" ||
  node.t === "array" ||
  node.t === "hash" ||
  node.t === "usermarshal" ||
  node.t === "extended" ||
  node.t === "userclass";

const isEditableScalar = (
  node: RgssRawNodeData,
): node is Extract<RgssRawNodeData, { t: RgssScalarType }> & { editable: true } =>
  (node.t === "int" || node.t === "float" || node.t === "bool" || node.t === "str") &&
  node.editable === true;

const formatKeyPreview = (node: RgssRawNodeData): string => {
  switch (node.t) {
    case "sym":
      return `:${node.v}`;
    case "str":
      return JSON.stringify(node.v);
    case "int":
    case "float":
    case "bool":
      return String(node.v);
    case "nil":
      return "nil";
    case "array":
      return `[${node.items.slice(0, 4).map(formatKeyPreview).join(", ")}${node.items.length > 4 ? ", …" : ""}]`;
    case "object":
    case "struct":
      return `${node.class}#${node.id}`;
    case "userdef":
      return `${node.class}(${node.size}B)`;
    case "link":
      return `-> #${node.to}`;
    default:
      return `#${node.id}`;
  }
};

const childEntries = (node: RgssRawNodeData): RgssRawEntry[] => {
  switch (node.t) {
    case "object":
    case "struct":
      return node.ivars.map(([name, value], index) => ({
        id: `${node.id}:iv:${index}`,
        label: name,
        node: value,
      }));
    case "array":
      return node.items.map((item, index) => ({
        id: `${node.id}:el:${index}`,
        label: `[${index}]`,
        node: item,
      }));
    case "hash": {
      const rows: RgssRawEntry[] = node.pairs.map(([key, value], index) => ({
        id: `${node.id}:p:${index}`,
        label: `${formatKeyPreview(key)} =>`,
        node: value,
      }));
      if (node.default) {
        rows.push({ id: `${node.id}:default`, label: "default =>", node: node.default });
      }
      return rows;
    }
    case "usermarshal":
      return [{ id: `${node.id}:val`, label: "value", node: node.value }];
    case "extended":
    case "userclass":
      return [{ id: `${node.id}:in`, label: "inner", node: node.inner }];
    default:
      return [];
  }
};

const containerSummary = (node: RgssRawNodeData): string => {
  switch (node.t) {
    case "object":
    case "struct":
      return `${node.class} (${node.ivars.length}) #${node.id}`;
    case "array":
      return `[${node.items.length}] #${node.id}`;
    case "hash":
      return `{${node.pairs.length}} #${node.id}`;
    case "usermarshal":
      return `U:${node.class} #${node.id}`;
    case "extended":
      return `extend(${node.module}) #${node.id}`;
    case "userclass":
      return `${node.class} #${node.id}`;
    default:
      return `#${node.id}`;
  }
};

const readonlyText = (node: RgssRawNodeData): string => {
  switch (node.t) {
    case "nil":
      return "nil";
    case "sym":
      return `:${node.v}`;
    case "link":
      return `-> #${node.to}`;
    case "userdef":
      return `${node.class} (${node.size} bytes)`;
    case "regexp":
      return `/${node.v}/`;
    case "classref":
      return `${node.kind}:${node.v}`;
    case "int":
    case "float":
    case "bool":
    case "str":
      return String(node.v);
    default:
      return node.t;
  }
};

const readonlyTitle = (node: RgssRawNodeData): string => {
  if (node.t === "link") return "Shared object link, read-only";
  if (node.t === "userdef") return "Opaque engine data, read-only";
  if (node.t === "sym") return "Symbol, read-only";
  if (node.t === "str") return "Non-UTF-8 string, read-only";
  return "Read-only";
};

const current = (nodeId: number, original: RawScalar): RawScalar => {
  const patch: RpgmPatch | undefined = props.patches.get(refKey(nodeId));
  return patch ? (patchValue(patch) as RawScalar) : original;
};

const shownText = (nodeId: number, original: RawScalar): string => draftText(props.drafts, rgssFieldId(nodeId), current(nodeId, original));
const errorOf = (nodeId: number): string => draftError(props.drafts, rgssFieldId(nodeId));
const invalidAttrs = (nodeId: number): Record<string, string> =>
  errorOf(nodeId) ? { "aria-invalid": "true", "aria-describedby": fieldErrorId(rgssFieldId(nodeId)) } : {};

const typed = (nodeId: number, type: RgssScalarType, original: RawScalar, event: Event): void => {
  emit("draft", nodeId, type, original, (event.target as HTMLInputElement | HTMLSelectElement).value);
};
</script>

<template>
  <ul class="raw-list" role="tree">
    <li
      v-for="entry in entries.slice(0, shown)"
      :key="entry.id"
      role="treeitem"
      :aria-expanded="isContainer(entry.node) ? open.has(entry.id) : undefined"
    >
      <div class="raw-row">
        <button
          v-if="isContainer(entry.node)"
          type="button"
          class="raw-toggle"
          :aria-label="`${open.has(entry.id) ? 'Collapse' : 'Expand'} ${entry.label}`"
          @click="toggle(entry.id)"
        >
          {{ open.has(entry.id) ? "▾" : "▸" }}
        </button>
        <span v-else class="raw-toggle"></span>
        <span class="raw-key">{{ entry.label }}</span>
        <template v-if="isContainer(entry.node)">
          <span class="raw-summary">{{ containerSummary(entry.node) }}</span>
        </template>
        <template v-else-if="isEditableScalar(entry.node)">
          <select
            v-if="entry.node.t === 'bool'"
            class="raw-input"
            :class="{ 'raw-dirty': patches.has(refKey(entry.node.id)) }"
            :value="String(current(entry.node.id, entry.node.v))"
            :aria-label="`Value of ${entry.label}`"
            @change="typed(entry.node.id, 'bool', entry.node.v, $event)"
            @blur="emit('settle', rgssFieldId(entry.node.id))"
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <input
            v-else
            class="raw-input"
            :class="{ 'raw-dirty': patches.has(refKey(entry.node.id)) }"
            :value="shownText(entry.node.id, entry.node.v as RawScalar)"
            :aria-label="`Value of ${entry.label}`"
            v-bind="invalidAttrs(entry.node.id)"
            @input="typed(entry.node.id, entry.node.t, entry.node.v as RawScalar, $event)"
            @blur="emit('settle', rgssFieldId(entry.node.id))"
          />
        </template>
        <span v-else class="raw-readonly" :title="readonlyTitle(entry.node)">{{ readonlyText(entry.node) }}</span>
        <span v-if="errorOf(entry.node.id)" :id="fieldErrorId(rgssFieldId(entry.node.id))" class="raw-error">{{ errorOf(entry.node.id) }}</span>
      </div>
      <RgssRawNode
        v-if="isContainer(entry.node) && open.has(entry.id)"
        :entries="childEntries(entry.node)"
        :patches="patches"
        :drafts="drafts"
        @draft="(nid, type, orig, text) => emit('draft', nid, type, orig, text)"
        @settle="(id) => emit('settle', id)"
      />
    </li>
    <li v-if="entries.length > shown">
      <button type="button" class="raw-more" @click="shown += MAX_ROWS">
        Show more ({{ entries.length - shown }} left)
      </button>
    </li>
  </ul>
</template>

<style scoped>
.raw-list { list-style: none; margin: 0; padding-left: 1rem; }
.raw-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.125rem 0; font-size: 0.75rem; flex-wrap: wrap; }
.raw-toggle { width: 1rem; color: var(--text-muted); background: none; border: 0; padding: 0; text-align: center; }
.raw-key { font-family: ui-monospace, monospace; color: var(--text-primary); }
.raw-summary, .raw-readonly { color: var(--text-muted); font-family: ui-monospace, monospace; }
.raw-input { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.375rem; padding: 0.125rem 0.5rem; color: var(--text-primary); font-family: ui-monospace, monospace; min-width: 6rem; }
.raw-input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
.raw-dirty { border-color: var(--brand); }
.raw-input[aria-invalid="true"] { border-color: var(--danger-text); }
.raw-error { color: var(--danger-text); overflow-wrap: anywhere; }
.raw-more { color: var(--brand); background: none; border: 0; font-size: 0.75rem; padding: 0.25rem 0; }
</style>
