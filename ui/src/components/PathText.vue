<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";

// Shows a file path that wraps after a "/" instead of in the middle of a name.
const props = defineProps<{ path: string }>();
const parts = computed(() => props.path.split(/(?<=\/)/));
</script>

<template>
  <span class="path-text"><template v-for="(part, index) in parts" :key="index">{{ part }}<wbr /></template></span>
</template>

<style scoped>
.path-text { overflow-wrap: anywhere; }
</style>
