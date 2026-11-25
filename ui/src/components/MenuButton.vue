<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script lang="ts">
import type { Component } from "vue";

export interface MenuButtonItem {
  id: string;
  label: string;
  icon?: Component;
  /** Extra classes for the icon, for example a play status tone. */
  iconClass?: string;
  /** Checked state; the item is a `menuitemradio` when the menu is a radio menu. */
  checked?: boolean;
  disabled?: boolean;
  /** Shown under the label and as the item title when the item is disabled. */
  disabledReason?: string;
  /** Heading of the group this item starts or continues; consecutive items with the same group share one heading. */
  group?: string;
}
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onBeforeUpdate, ref, useId, useTemplateRef, watch } from "vue";
import { IconCheck, IconChevronDown } from "@tabler/icons-vue";
import { toCssPx } from "../utils/interfaceScalePreference";
import { initialMenuIndex, menuKeyAction } from "../utils/menuKeyboard";

const props = withDefaults(defineProps<{
  items: MenuButtonItem[];
  /** Accessible name of the menu itself. */
  menuLabel: string;
  /** Accessible name of the button that opens the menu (the chevron in split mode). */
  buttonLabel?: string | undefined;
  buttonTitle?: string | undefined;
  /** Split mode: a main action button plus a separate menu button. */
  split?: boolean;
  mainLabel?: string | undefined;
  mainTitle?: string | undefined;
  mainDisabled?: boolean | undefined;
  /** Items are `menuitemradio` with `aria-checked`. */
  radio?: boolean;
  align?: "start" | "end";
  disabled?: boolean | undefined;
  /** Two columns of items per group: a long menu stays short enough to read without scrolling. */
  columns?: 1 | 2;
}>(), { align: "start", columns: 1 });

const emit = defineEmits<{ select: [id: string]; main: [] }>();

const open = ref(false);
const root = useTemplateRef<HTMLElement>("root");
const toggleButton = useTemplateRef<HTMLButtonElement>("toggleButton");
let itemRefs: HTMLButtonElement[] = [];
onBeforeUpdate(() => { itemRefs = []; });
function setItemRef(el: unknown, index: number) {
  if (el instanceof HTMLButtonElement) itemRefs[index] = el;
}
const menuId = useId();
const disabledFlags = computed(() => props.items.map(item => !!item.disabled));
// Consecutive items with the same group render under one heading.
const sections = computed(() => {
  const list: { label: string; items: { item: MenuButtonItem; index: number }[] }[] = [];
  props.items.forEach((item, index) => {
    const label = item.group ?? "";
    const last = list[list.length - 1];
    if (last && last.label === label) last.items.push({ item, index });
    else list.push({ label, items: [{ item, index }] });
  });
  return list;
});

// The panel opens below the trigger. It moves left or right to stay inside the
// window and opens upward when there is more room above, so a narrow or short
// window never cuts it off. Rectangles use viewport pixels; styles use CSS pixels.
const panel = useTemplateRef<HTMLElement>("panel");
const panelStyle = ref<Record<string, string>>({});
const openUp = ref(false);
const EDGE = 8;
function placePanel() {
  const trigger = root.value?.getBoundingClientRect();
  if (!trigger || !panel.value) return;
  const below = window.innerHeight - trigger.bottom - EDGE;
  const above = trigger.top - EDGE;
  const height = panel.value.scrollHeight;
  openUp.value = height > below && above > below;
  const room = Math.max(120, (openUp.value ? above : below) - 6);
  const rect = panel.value.getBoundingClientRect();
  let shift = 0;
  if (rect.right > window.innerWidth - EDGE) shift = window.innerWidth - EDGE - rect.right;
  if (rect.left + shift < EDGE) shift = EDGE - rect.left;
  panelStyle.value = {
    maxHeight: `min(${props.columns === 2 ? "40rem" : "20rem"}, ${toCssPx(room)}px)`,
    ...(shift ? { transform: `translateX(${toCssPx(shift)}px)` } : {}),
  };
}

function focusItem(index: number) {
  if (index >= 0) itemRefs[index]?.focus();
}

function openMenu() {
  if (open.value || props.disabled) return;
  open.value = true;
  document.addEventListener("pointerdown", onOutsidePointer, true);
  window.addEventListener("resize", onWindowResize);
  void nextTick(() => {
    placePanel();
    focusItem(initialMenuIndex(disabledFlags.value, props.items.findIndex(item => item.checked)));
  });
}

function closeMenu(returnFocus = true) {
  if (!open.value) return;
  open.value = false;
  panelStyle.value = {};
  openUp.value = false;
  document.removeEventListener("pointerdown", onOutsidePointer, true);
  window.removeEventListener("resize", onWindowResize);
  if (returnFocus) void nextTick(() => toggleButton.value?.focus());
}

function toggleMenu() {
  if (open.value) closeMenu();
  else openMenu();
}

function onOutsidePointer(event: Event) {
  if (event.target instanceof Node && root.value?.contains(event.target)) return;
  closeMenu(false);
}

function onWindowResize() {
  closeMenu(false);
}

function onToggleKeydown(event: KeyboardEvent) {
  if (open.value || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
  event.preventDefault();
  // The opening key must not reach onMenuKeydown on the root: the item focus
  // queued by openMenu can run first, and that handler would then move focus
  // one item further.
  event.stopPropagation();
  openMenu();
}

function onMenuKeydown(event: KeyboardEvent) {
  if (!open.value) return;
  const current = itemRefs.indexOf(document.activeElement as HTMLButtonElement);
  const action = menuKeyAction(event.key, current, disabledFlags.value);
  if (!action) return;
  if (action.type === "close") {
    if (action.returnFocus) { event.preventDefault(); event.stopPropagation(); }
    closeMenu(action.returnFocus);
    return;
  }
  event.preventDefault();
  focusItem(action.index);
}

function choose(item: MenuButtonItem) {
  if (item.disabled) return;
  closeMenu();
  emit("select", item.id);
}

watch(() => props.disabled, disabled => { if (disabled) closeMenu(false); });
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onOutsidePointer, true);
  window.removeEventListener("resize", onWindowResize);
});

defineExpose({ focus: () => toggleButton.value?.focus(), close: closeMenu });
</script>

<template>
  <div ref="root" class="menu-button" :class="{ 'menu-button--split': split, 'menu-button--open': open }" @keydown="onMenuKeydown">
    <button
      v-if="split"
      type="button"
      class="menu-button-trigger menu-button-main"
      :aria-label="mainLabel"
      :title="mainTitle"
      :disabled="mainDisabled"
      @click="emit('main')"
    >
      <slot name="main" />
    </button>
    <button
      ref="toggleButton"
      type="button"
      class="menu-button-trigger menu-button-toggle"
      :aria-label="buttonLabel"
      :title="buttonTitle ?? (split ? buttonLabel : undefined)"
      aria-haspopup="menu"
      :aria-expanded="open"
      :aria-controls="open ? menuId : undefined"
      :disabled="disabled"
      @click="toggleMenu"
      @keydown="onToggleKeydown"
    >
      <slot />
      <IconChevronDown class="menu-button-chevron" aria-hidden="true" />
    </button>
    <div
      v-if="open"
      :id="menuId"
      ref="panel"
      role="menu"
      :aria-label="menuLabel"
      class="menu-button-panel"
      :class="[align === 'end' ? 'menu-button-panel--end' : 'menu-button-panel--start', { 'menu-button-panel--up': openUp, 'menu-button-panel--columns': columns === 2 }]"
      :style="panelStyle"
    >
      <div
        v-for="(section, sectionIndex) in sections"
        :key="`${sectionIndex}-${section.label}`"
        :role="section.label ? 'group' : 'none'"
        :aria-labelledby="section.label ? `${menuId}-group-${sectionIndex}` : undefined"
        class="menu-button-section"
      >
        <div v-if="section.label" :id="`${menuId}-group-${sectionIndex}`" role="presentation" class="menu-button-group-label">{{ section.label }}</div>
        <button
          v-for="{ item, index } in section.items"
          :key="item.id"
          :ref="el => setItemRef(el, index)"
          type="button"
          :role="radio ? 'menuitemradio' : 'menuitem'"
          :aria-checked="radio ? !!item.checked : undefined"
          :aria-describedby="item.disabled && item.disabledReason ? `${menuId}-reason-${index}` : undefined"
          :title="item.disabled ? item.disabledReason : undefined"
          :disabled="item.disabled"
          class="menu-button-item"
          :class="{ 'menu-button-item--checked': item.checked }"
          @click="choose(item)"
        >
          <component :is="item.icon" v-if="item.icon" class="menu-button-item-icon" :class="item.iconClass" aria-hidden="true" />
          <span class="menu-button-item-text">
            <span>{{ item.label }}</span>
            <span v-if="item.disabled && item.disabledReason" :id="`${menuId}-reason-${index}`" class="menu-button-item-reason">{{ item.disabledReason }}</span>
          </span>
          <IconCheck v-if="radio && item.checked" class="menu-button-item-check" aria-hidden="true" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.menu-button { position: relative; display: inline-flex; align-items: stretch; flex-shrink: 0; }
.menu-button-trigger {
  display: inline-flex; align-items: center; justify-content: center; gap: .4rem;
  min-height: 2.375rem; padding: .5rem .75rem; white-space: nowrap;
  border: 1px solid var(--border); border-radius: .5rem;
  background: var(--bg-raised); color: var(--text-primary);
  font-size: .75rem; font-weight: 500;
  transition: background-color .15s ease, border-color .15s ease, color .15s ease;
}
.menu-button-trigger:hover:not(:disabled) { background: var(--bg-overlay); border-color: var(--border-hover); }
.menu-button-trigger:disabled { opacity: .6; cursor: not-allowed; }
.menu-button--split .menu-button-main { border-top-right-radius: 0; border-bottom-right-radius: 0; }
.menu-button--split .menu-button-toggle { border-top-left-radius: 0; border-bottom-left-radius: 0; margin-left: -1px; padding-inline: .5rem; }
.menu-button-chevron { width: 1rem; height: 1rem; flex-shrink: 0; transition: transform .15s ease; }
.menu-button--open .menu-button-chevron { transform: rotate(180deg); }
.menu-button-panel {
  position: absolute; z-index: 40; top: calc(100% + .3rem);
  min-width: 100%; width: max-content; max-width: min(22rem, calc(var(--app-vw) - 16px)); max-height: 20rem; overflow-y: auto;
  padding: .375rem; border: 1px solid var(--border); border-radius: .5rem;
  background: var(--bg-surface); box-shadow: 0 6px 20px #0003;
}
.menu-button-panel--start { left: 0; }
.menu-button-panel--end { right: 0; }
.menu-button-panel--up { top: auto; bottom: calc(100% + .3rem); }
.menu-button-panel--columns { width: 19rem; max-width: calc(var(--app-vw) - 16px); }
.menu-button-panel--columns .menu-button-section { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: .25rem; }
.menu-button-panel--columns .menu-button-group-label { grid-column: 1 / -1; }
.menu-button-section + .menu-button-section { margin-top: .25rem; padding-top: .25rem; border-top: 1px solid var(--border); }
.menu-button-group-label {
  padding: .375rem .5rem .25rem;
  color: var(--text-muted); font-size: .75rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
}
.menu-button-item {
  display: flex; align-items: center; gap: .5rem; width: 100%;
  padding: .5rem; border-radius: .3rem; text-align: left;
  color: var(--text-primary); font-size: .8125rem;
}
.menu-button-item:hover:not(:disabled), .menu-button-item:focus-visible { background: var(--bg-overlay); }
.menu-button-item:disabled { color: var(--text-muted); cursor: not-allowed; }
.menu-button-item--checked { font-weight: 600; }
.menu-button-item-icon { width: 1rem; height: 1rem; flex-shrink: 0; }
.menu-button-item-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.menu-button-item-reason { font-size: .75rem; color: var(--text-muted); }
.menu-button-item-check { width: 1rem; height: 1rem; flex-shrink: 0; color: var(--brand); }
</style>
