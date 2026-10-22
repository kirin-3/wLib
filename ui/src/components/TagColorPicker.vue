<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { TAG_COLOR_SWATCHES, colorForTag, setTagColor, type TagSwatch } from "../utils/tagColors";
import { notifyError } from "../utils/toast";

const props = defineProps<{ tag: string; anchor: HTMLElement }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
const left = ref(8);
const top = ref(8);
const options = [...TAG_COLOR_SWATCHES, null];
const active = ref(Math.max(0, options.indexOf(colorForTag(props.tag) || null)));
const buttons = () => [...(panel.value?.querySelectorAll<HTMLButtonElement>("[role=menuitemradio]") || [])];

function close(restore = true) {
  emit("close");
  if (restore) props.anchor.focus();
}
async function choose(swatch: TagSwatch | null) {
  close();
  try { await setTagColor(props.tag, swatch); }
  catch (error) { notifyError(error instanceof Error ? error.message : String(error), "Could not save tag color"); }
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
  const moves: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 4, ArrowUp: -4 };
  if (event.key in moves || event.key === "Home" || event.key === "End") {
    event.preventDefault();
    active.value = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (active.value + (moves[event.key] || 0) + options.length) % options.length;
    buttons()[active.value]?.focus();
  }
  if (event.key === "Tab") close();
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !panel.value?.contains(event.target)) close(false);
}
onMounted(async () => {
  await nextTick();
  const rect = props.anchor.getBoundingClientRect();
  const bounds = panel.value?.getBoundingClientRect();
  left.value = Math.max(8, Math.min(rect.left, window.innerWidth - (bounds?.width || 280) - 8));
  top.value = Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - (bounds?.height || 200) - 8));
  buttons()[active.value]?.focus();
  document.addEventListener("pointerdown", outside, true);
});
onBeforeUnmount(() => document.removeEventListener("pointerdown", outside, true));
</script>

<template>
  <Teleport to="body">
    <div ref="panel" class="tag-color-picker" :style="{ left: `${left}px`, top: `${top}px` }"
      role="menu" :aria-label="`Color for ${tag}`" @keydown="keydown">
      <p class="picker-label">Color for {{ tag }}</p>
      <div class="swatch-grid">
        <button v-for="(swatch, index) in options" :key="swatch || 'none'" type="button"
          role="menuitemradio" :aria-checked="(colorForTag(tag) || null) === swatch"
          :tabindex="active === index ? 0 : -1" class="tag-swatch" :class="{ 'no-color': !swatch }"
          :data-tag-color="swatch || undefined" @focus="active = index" @click="choose(swatch)">
          <span aria-hidden="true" class="selection-mark">{{ (colorForTag(tag) || null) === swatch ? '✓' : '' }}</span>
          {{ swatch ? swatch[0]!.toUpperCase() + swatch.slice(1) : 'No color' }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.tag-color-picker { position: fixed; z-index: 10000; width: min(22rem, calc(100vw - 16px)); padding: 0.75rem; border: 1px solid var(--text-secondary); border-radius: 0.5rem; background: var(--bg-surface); color: var(--text-primary); box-shadow: var(--shadow-modal); }
.picker-label { font-size: 0.875rem; font-weight: 600; margin-bottom: 0.75rem; overflow-wrap: anywhere; }
.swatch-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.5rem; }
.tag-swatch { min-height: 44px; border-radius: 0.375rem; font-size: 0.75rem; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.selection-mark { height: 1rem; line-height: 1rem; font-weight: 700; }
.no-color { grid-column: 1 / -1; flex-direction: row; gap: 0.5rem; color: var(--text-primary); background: var(--bg-raised); border: 1px solid var(--text-secondary); }
</style>
