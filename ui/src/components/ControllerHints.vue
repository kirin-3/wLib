<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { currentHints, defaultHints, type ControllerHint } from "../utils/controllerActions";
const hints = ref<ControllerHint[]>(defaultHints);
const update = () => { hints.value = currentHints(); };
onMounted(() => { update(); document.addEventListener("focusin", update); window.addEventListener("wlib-controller-action", update); });
onUnmounted(() => { document.removeEventListener("focusin", update); window.removeEventListener("wlib-controller-action", update); });
</script>
<template>
  <div class="controller-hints" aria-label="Controller shortcuts">
    <span v-for="hint in hints" :key="hint.button"><kbd>{{ hint.button }}</kbd> {{ hint.label }}</span>
  </div>
</template>
<style scoped>
.controller-hints { position: fixed; z-index: 10000; bottom: 0; left: 0; right: 0; display: flex; flex-wrap: wrap; gap: .35rem 1rem; padding: .4rem .8rem; background: var(--bg-surface); color: var(--text-primary); border-top: 1px solid var(--border); font-size: .75rem; }
kbd { display: inline-block; padding: .1rem .35rem; border-radius: .3rem; background: var(--bg-raised); border: 1px solid var(--border); font: inherit; font-weight: 700; }
</style>
