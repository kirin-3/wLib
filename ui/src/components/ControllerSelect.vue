<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { toCssPx } from "../utils/interfaceScalePreference";
const source = ref<HTMLSelectElement | null>(null);
const overlay = ref<HTMLElement | null>(null);
const position = ref({ x: 0, y: 0 });
const options = ref<{ value: string; text: string; disabled: boolean }[]>([]);
function close(restoreFocus = true) { const element = source.value; source.value = null; if (restoreFocus) element?.focus(); }
function pointerOutside(event: PointerEvent) { if (source.value && !overlay.value?.contains(event.target as Node)) close(false); }
function choose(index: number) {
  const element = source.value, option = options.value[index];
  if (!element || !option || option.disabled) return;
  element.selectedIndex = index;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  close();
}
async function open(event: Event) {
  const element = (event as CustomEvent<HTMLSelectElement>).detail;
  if (!element || element.disabled) return;
  source.value = element;
  options.value = Array.from(element.options, option => ({ value: option.value, text: option.text, disabled: option.disabled || !!option.closest("optgroup:disabled") }));
  const rect = element.getBoundingClientRect();
  position.value = { x: rect.left, y: rect.bottom };
  await nextTick();
  if (!overlay.value) return;
  const bounds = overlay.value.getBoundingClientRect();
  position.value = { x: Math.max(8, Math.min(rect.left, window.innerWidth - bounds.width - 8)), y: Math.max(8, Math.min(rect.bottom, window.innerHeight - bounds.height - 8)) };
  const buttons = overlay.value.querySelectorAll<HTMLButtonElement>("button");
  (buttons[element.selectedIndex] ?? overlay.value.querySelector<HTMLButtonElement>("button:not(:disabled)"))?.focus();
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault(); event.stopPropagation();
  const buttons = [...(overlay.value?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
  const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
  buttons[Math.max(0, Math.min(buttons.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
}
onMounted(() => { window.addEventListener("wlib-controller-select", open); window.addEventListener("pointerdown", pointerOutside, true); });
onUnmounted(() => { window.removeEventListener("wlib-controller-select", open); window.removeEventListener("pointerdown", pointerOutside, true); });
</script>
<template>
  <Teleport to="body">
    <div v-if="source" ref="overlay" data-controller-select role="listbox" class="controller-select" :style="{ left: `${toCssPx(position.x)}px`, top: `${toCssPx(position.y)}px` }" @keydown="keydown">
      <button v-for="(option, i) in options" :key="i" type="button" role="option" :aria-selected="i === source.selectedIndex" :disabled="option.disabled" @click="choose(i)">{{ option.text }}</button>
    </div>
  </Teleport>
</template>
<style scoped>
.controller-select { position: fixed; z-index: 9999; min-width: 12rem; max-height: 50vh; overflow-y: auto; padding: .25rem; background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border); border-radius: .5rem; box-shadow: 0 .5rem 2rem #0005; }
.controller-select button { display: block; width: 100%; padding: .5rem .75rem; text-align: left; border-radius: .25rem; }
.controller-select button:focus, .controller-select button:hover { background: var(--bg-raised); }
.controller-select button:disabled { opacity: .45; }
</style>
