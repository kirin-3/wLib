<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One filter pill. A click emits `activate`; the parent decides what it does
// (the Library and Discover toggle include).
// With `excludable`, a "−" button shows on hover and keyboard focus, and
// Alt+click or the minus key on the pill emit `exclude` too.
// Other listeners and attributes, such as `data-tag` and the tag pill's
// contextmenu and Shift+F10 handlers, go to the main button, not the wrapper.
import { IconBan, IconCheck, IconMinus } from "@tabler/icons-vue";
import type { FilterValueState } from "../utils/libraryGames";

defineOptions({ inheritAttrs: false });

const props = defineProps<{
  label: string;
  state: FilterValueState;
  count?: number | undefined;
  color?: string | undefined;
  excludable?: boolean | undefined;
}>();

const emit = defineEmits<{ activate: []; exclude: [] }>();

function onClick(event: MouseEvent): void {
  if (props.excludable && event.altKey) emit("exclude");
  else emit("activate");
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.excludable || event.key !== "-" || event.ctrlKey || event.metaKey) return;
  event.preventDefault();
  emit("exclude");
}
</script>

<template>
  <span
    class="filter-pill"
    :class="[`filter-pill--${props.state}`, { 'filter-pill--excludable': props.excludable }]"
    :data-tag-color="props.color || undefined"
    :data-filter-state="props.state"
  >
    <button
      type="button"
      v-bind="$attrs"
      class="filter-pill-main"
      :aria-pressed="props.state === 'include'"
      @click="onClick"
      @keydown="onKeydown"
    >
      <IconCheck v-if="props.state === 'include'" class="filter-pill-icon" aria-hidden="true" />
      <IconBan v-else-if="props.state === 'exclude'" class="filter-pill-icon" aria-hidden="true" />
      <span v-else-if="props.color" class="filter-pill-dot" aria-hidden="true"></span>
      <slot v-else name="icon" />
      <span class="filter-pill-label">{{ props.label }}</span>
      <span v-if="props.count !== undefined" class="filter-pill-count">{{ props.count }}</span>
      <!-- The pill is relative so this absolute span stays inside the filter
           pane's scroll area; otherwise it can stretch <main> past the window. -->
      <span v-if="props.state === 'exclude'" class="sr-only">, excluded</span>
    </button>
    <button
      v-if="props.excludable"
      type="button"
      class="filter-pill-exclude"
      :aria-label="`Exclude ${props.label}`"
      :aria-pressed="props.state === 'exclude'"
      :title="props.state === 'exclude' ? `Stop excluding ${props.label}` : `Exclude ${props.label} (Alt+click the pill, or press −)`"
      @click="emit('exclude')"
    >
      <IconMinus class="w-3 h-3" aria-hidden="true" stroke-width="2.5" />
    </button>
  </span>
</template>

<style scoped>
.filter-pill {
  position: relative;
  display: inline-flex;
  max-width: 100%;
  border: 1px solid var(--border);
  border-radius: 9999px;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 500;
  line-height: 1.25;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.filter-pill:hover {
  border-color: var(--border-hover);
  color: var(--text-primary);
}
.filter-pill--include {
  border-color: var(--brand);
  background: var(--brand-glow-strong);
  color: var(--text-primary);
}
.filter-pill--include:hover {
  border-color: var(--brand);
}
.filter-pill--include .filter-pill-icon {
  color: var(--brand);
}
/* A tag color fills the pill only once the tag is included. */
.filter-pill--include[data-tag-color] {
  border-color: var(--tag-border);
  background: var(--tag-bg);
  color: var(--tag-text);
  box-shadow: inset 0 0 0 1px var(--tag-border);
}
.filter-pill--include[data-tag-color] .filter-pill-icon {
  color: var(--tag-text);
}
.filter-pill--exclude,
.filter-pill--exclude:hover {
  border-color: var(--danger-text);
  background: var(--danger-bg);
  color: var(--danger-text);
}
.filter-pill--exclude .filter-pill-label {
  text-decoration: line-through;
  text-decoration-thickness: 1.5px;
}
.filter-pill-main {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  min-width: 0;
  padding: 0.25rem 0.625rem;
  border-radius: inherit;
  color: inherit;
  text-align: left;
}
.filter-pill-main:focus-visible {
  outline: 2px solid var(--text-primary);
  outline-offset: 2px;
}
.filter-pill-label {
  min-width: 0;
  overflow-wrap: anywhere;
}
.filter-pill-icon,
.filter-pill-main :slotted(svg) {
  width: 0.8125rem;
  height: 0.8125rem;
  flex-shrink: 0;
}
.filter-pill-dot {
  width: 0.5rem;
  height: 0.5rem;
  flex-shrink: 0;
  border-radius: 9999px;
  background: var(--tag-border);
}
/* The count is quieter than the label through a color token, not opacity,
   so it keeps 4.5:1 on the pill in both themes. A colored pill (included
   tag, excluded value) keeps its own text color. */
.filter-pill-count {
  color: var(--text-muted);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
.filter-pill--include .filter-pill-count {
  color: var(--text-secondary);
}
.filter-pill--include[data-tag-color] .filter-pill-count,
.filter-pill--exclude .filter-pill-count {
  color: inherit;
}
/* The exclude button sits on the pill's corner so showing it never moves the
   pills around it. Hidden (not only transparent) keeps it out of Tab and
   controller focus until the pill is hovered or focused. */
.filter-pill-exclude {
  position: absolute;
  top: -0.5rem;
  right: -0.5rem;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 1.25rem;
  height: 1.25rem;
  border: 1px solid var(--border-hover);
  border-radius: 9999px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  box-shadow: 0 1px 3px rgb(0 0 0 / 0.25);
  visibility: hidden;
  opacity: 0;
  /* Hiding waits for the fade; showing is instant, so Tab from the pill
     reaches the button at once. */
  transition: opacity 0.12s ease-out, visibility 0s linear 0.12s, background-color 0.15s ease, color 0.15s ease;
}
.filter-pill--excludable:hover .filter-pill-exclude,
.filter-pill--excludable:focus-within .filter-pill-exclude {
  visibility: visible;
  opacity: 1;
  transition: opacity 0.12s ease-out, visibility 0s, background-color 0.15s ease, color 0.15s ease;
}
/* The danger tint is see-through; the surface under it keeps the button
   opaque over the pill edge. */
.filter-pill-exclude:hover,
.filter-pill-exclude[aria-pressed="true"] {
  border-color: var(--danger-text);
  background: linear-gradient(var(--danger-bg), var(--danger-bg)), var(--bg-surface);
  color: var(--danger-text);
}
.filter-pill-exclude:focus-visible {
  outline: 2px solid var(--text-primary);
  outline-offset: 1px;
}
@media (pointer: coarse) {
  .filter-pill-main { min-height: 44px; }
  .filter-pill-exclude { width: 1.75rem; height: 1.75rem; top: -0.75rem; right: -0.75rem; }
}
@media (prefers-reduced-motion: reduce) {
  .filter-pill,
  .filter-pill-exclude { transition: none; }
}
</style>
