<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import type { Component } from "vue";

// Shared frame for the onboarding steps with an icon header (catalog,
// LewdCorner, import, extension). The overlay makes the header smaller in
// compact and TV layouts through these class names.
const props = defineProps<{
  icon: Component;
  title: string;
  lead: string;
}>();
</script>

<template>
  <div class="step-shell">
    <header class="step-header">
      <div class="step-icon-tile" aria-hidden="true">
        <component :is="props.icon" class="w-6 h-6" />
      </div>
      <div class="min-w-0">
        <h2 id="onboarding-step-title" tabindex="-1" class="step-title">
          {{ props.title }}
        </h2>
        <p class="step-lead">{{ props.lead }}</p>
      </div>
    </header>
    <slot />
  </div>
</template>

<style scoped>
.step-shell {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.step-header {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.step-icon-tile {
  width: 3rem;
  height: 3rem;
  border-radius: 0.75rem;
  background: var(--brand-glow);
  color: var(--brand-text);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.step-title {
  font-size: 1.5rem;
  line-height: 1.25;
  font-weight: 700;
  color: var(--text-primary);
  text-wrap: balance;
  outline: none;
}

.step-lead {
  margin-top: 0.35rem;
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--text-secondary);
  text-wrap: pretty;
  max-width: 38rem;
}
</style>
