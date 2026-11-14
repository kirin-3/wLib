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
}
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onBeforeUpdate, ref, useId, watch } from "vue";
import { IconCheck, IconChevronDown } from "@tabler/icons-vue";
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
}>(), { align: "start" });

const emit = defineEmits<{ select: [id: string]; main: [] }>();

const open = ref(false);
const root = ref<HTMLElement | null>(null);
const toggleButton = ref<HTMLButtonElement | null>(null);
let itemRefs: HTMLButtonElement[] = [];
onBeforeUpdate(() => { itemRefs = []; });
function setItemRef(el: unknown, index: number) {
  if (el instanceof HTMLButtonElement) itemRefs[index] = el;
}
const menuId = useId();
const disabledFlags = computed(() => props.items.map(item => !!item.disabled));

function focusItem(index: number) {
  if (index >= 0) itemRefs[index]?.focus();
}

function openMenu() {
  if (open.value || props.disabled) return;
  open.value = true;
  document.addEventListener("pointerdown", onOutsidePointer, true);
  void nextTick(() => focusItem(initialMenuIndex(disabledFlags.value, props.items.findIndex(item => item.checked))));
}

function closeMenu(returnFocus = true) {
  if (!open.value) return;
  open.value = false;
  document.removeEventListener("pointerdown", onOutsidePointer, true);
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

function onToggleKeydown(event: KeyboardEvent) {
  if (open.value || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
  event.preventDefault();
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
onBeforeUnmount(() => document.removeEventListener("pointerdown", onOutsidePointer, true));

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
      role="menu"
      :aria-label="menuLabel"
      class="menu-button-panel"
      :class="align === 'end' ? 'menu-button-panel--end' : 'menu-button-panel--start'"
    >
      <button
        v-for="(item, index) in items"
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
  min-width: 100%; width: max-content; max-width: min(22rem, 90vw); max-height: 20rem; overflow-y: auto;
  padding: .375rem; border: 1px solid var(--border); border-radius: .5rem;
  background: var(--bg-surface); box-shadow: 0 6px 20px #0003;
}
.menu-button-panel--start { left: 0; }
.menu-button-panel--end { right: 0; }
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
.menu-button-item-reason { font-size: .6875rem; color: var(--text-muted); }
.menu-button-item-check { width: 1rem; height: 1rem; flex-shrink: 0; color: var(--brand); }
</style>
