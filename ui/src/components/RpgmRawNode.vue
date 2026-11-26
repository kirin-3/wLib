<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref } from "vue";
import type { RpgmPatch } from "../services/api";
import {
  draftError,
  draftText,
  fieldErrorId,
  patchValue,
  pathKey,
  rawChildren,
  rawFieldId,
  type DraftMap,
  type PatchMap,
  type RawScalar,
  type RpgmPath,
} from "../utils/rpgmSaveEditor";

defineOptions({ name: "RpgmRawNode" });

const props = defineProps<{
  node: unknown;
  path: RpgmPath;
  patches: PatchMap;
  // Typed text and errors, owned by the editor so an error blocks Save
  drafts: DraftMap;
}>();
// The editor checks the text against the original value's type.
const emit = defineEmits<{ draft: [path: RpgmPath, original: RawScalar, text: string]; settle: [id: string] }>();

const MAX_ROWS = 200;
const open = ref<Set<string | number>>(new Set());
const shown = ref(MAX_ROWS);

const toggle = (key: string | number): void => {
  const next = new Set(open.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  open.value = next;
};

const current = (key: string | number, original: unknown): unknown => {
  const patch: RpgmPatch | undefined = props.patches.get(pathKey([...props.path, key]));
  return patch ? patchValue(patch) : original;
};

const fieldId = (key: string | number): string => rawFieldId([...props.path, key]);
const shownText = (key: string | number, original: unknown): string => draftText(props.drafts, fieldId(key), current(key, original));
const errorOf = (key: string | number): string => draftError(props.drafts, fieldId(key));
const invalidAttrs = (key: string | number): Record<string, string> =>
  errorOf(key) ? { "aria-invalid": "true", "aria-describedby": fieldErrorId(fieldId(key)) } : {};

const typed = (key: string | number, original: RawScalar, event: Event): void => {
  emit("draft", [...props.path, key], original, (event.target as HTMLInputElement | HTMLSelectElement).value);
};

const preview = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.length}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).length}}`;
  return String(value);
};
</script>

<template>
  <ul class="raw-list" role="tree">
    <li v-for="child in rawChildren(node).slice(0, shown)" :key="String(child.key)" role="treeitem" :aria-expanded="child.container ? open.has(child.key) : undefined">
      <div class="raw-row">
        <button v-if="child.container" type="button" class="raw-toggle" :aria-label="`${open.has(child.key) ? 'Collapse' : 'Expand'} ${child.key}`" @click="toggle(child.key)">
          {{ open.has(child.key) ? "▾" : "▸" }}
        </button>
        <span v-else class="raw-toggle"></span>
        <span class="raw-key" :class="{ 'raw-meta': child.meta }">{{ child.key }}</span>
        <template v-if="child.container">
          <span class="raw-summary">{{ preview(child.value) }}</span>
        </template>
        <template v-else-if="child.editable">
          <input
            v-if="typeof child.value !== 'boolean'"
            class="raw-input"
            :class="{ 'raw-dirty': patches.has(pathKey([...path, child.key])) }"
            :value="shownText(child.key, child.value)"
            :aria-label="`Value of ${child.key}`"
            v-bind="invalidAttrs(child.key)"
            @input="typed(child.key, child.value as RawScalar, $event)"
            @blur="emit('settle', fieldId(child.key))"
          />
          <select
            v-else
            class="raw-input"
            :class="{ 'raw-dirty': patches.has(pathKey([...path, child.key])) }"
            :value="String(current(child.key, child.value))"
            :aria-label="`Value of ${child.key}`"
            @change="typed(child.key, child.value as RawScalar, $event)"
            @blur="emit('settle', fieldId(child.key))"
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </template>
        <span v-else class="raw-readonly" :title="child.meta ? 'Used by RPG Maker, read-only' : 'Read-only'">{{ child.value === null ? "null" : preview(child.value) }}</span>
        <span v-if="errorOf(child.key)" :id="fieldErrorId(fieldId(child.key))" class="raw-error">{{ errorOf(child.key) }}</span>
      </div>
      <RpgmRawNode
        v-if="child.container && open.has(child.key)"
        :node="child.value"
        :path="[...path, child.key]"
        :patches="patches"
        :drafts="drafts"
        @draft="(p, o, t) => emit('draft', p, o, t)"
        @settle="(id) => emit('settle', id)"
      />
    </li>
    <li v-if="rawChildren(node).length > shown">
      <button type="button" class="raw-more" @click="shown += MAX_ROWS">Show more ({{ rawChildren(node).length - shown }} left)</button>
    </li>
  </ul>
</template>

<style scoped>
.raw-list { list-style: none; margin: 0; padding-left: 1rem; }
.raw-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.125rem 0; font-size: 0.75rem; flex-wrap: wrap; }
.raw-toggle { width: 1rem; color: var(--text-muted); background: none; border: 0; padding: 0; text-align: center; }
.raw-key { font-family: ui-monospace, monospace; color: var(--text-primary); }
.raw-meta { color: var(--text-muted); }
.raw-summary, .raw-readonly { color: var(--text-muted); font-family: ui-monospace, monospace; }
.raw-input { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 0.375rem; padding: 0.125rem 0.5rem; color: var(--text-primary); font-family: ui-monospace, monospace; min-width: 6rem; }
.raw-input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
.raw-dirty { border-color: var(--brand); }
.raw-input[aria-invalid="true"] { border-color: var(--danger-text); }
.raw-error { color: var(--danger-text); overflow-wrap: anywhere; }
.raw-more { color: var(--brand); background: none; border: 0; font-size: 0.75rem; padding: 0.25rem 0; }
</style>
