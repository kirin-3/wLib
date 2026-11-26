<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconCheck } from "@tabler/icons-vue";
import type { OnboardingStepId } from "../../services/api";
import { ONBOARDING_STEPS, stepIndex, stepMeta, stepNumber } from "../../utils/onboarding";

const props = defineProps<{
  step: OnboardingStepId;
  compact: boolean;
}>();

const currentIdx = computed(() => stepIndex(props.step));
const currentNum = computed(() => stepNumber(props.step));
const currentMeta = computed(() => stepMeta(props.step));
const progressPercent = computed(() => {
  const spans = ONBOARDING_STEPS.length - 1;
  return spans > 0 ? (currentIdx.value / spans) * 100 : 0;
});
</script>

<template>
  <!-- Compact top bar for narrow windows and compact TV widths -->
  <div v-if="props.compact" class="rail-compact" aria-label="Setup progress">
    <div class="rail-compact-head">
      <div class="rail-brand-mini">
        <img src="/icon.svg" alt="" class="w-5 h-5 shrink-0" aria-hidden="true" />
        <span class="rail-compact-step">
          Step {{ currentNum }} of {{ ONBOARDING_STEPS.length }} · {{ currentMeta.label }}
        </span>
      </div>
    </div>
    <div class="rail-segments" aria-hidden="true">
      <span
        v-for="(item, idx) in ONBOARDING_STEPS"
        :key="item.id"
        class="rail-segment"
        :class="{
          'rail-segment--done': idx < currentIdx,
          'rail-segment--current': idx === currentIdx,
        }"
      />
    </div>
  </div>

  <!-- Wide vertical step rail -->
  <aside v-else class="rail-wide" aria-label="Setup steps">
    <div class="rail-brand">
      <img src="/icon.svg" alt="" class="w-7 h-7 shrink-0" aria-hidden="true" />
      <div class="min-w-0">
        <p class="rail-brand-title">wLib setup</p>
        <p class="rail-brand-count">
          Step {{ currentNum }} of {{ ONBOARDING_STEPS.length }}
        </p>
      </div>
    </div>

    <ol class="rail-list">
      <div class="rail-track" aria-hidden="true">
        <div class="rail-track-fill" :style="{ height: `${progressPercent}%` }" />
      </div>
      <li
        v-for="(item, idx) in ONBOARDING_STEPS"
        :key="item.id"
        class="rail-item"
        :class="{
          'rail-item--done': idx < currentIdx,
          'rail-item--current': idx === currentIdx,
          'rail-item--upcoming': idx > currentIdx,
        }"
        :aria-current="idx === currentIdx ? 'step' : undefined"
      >
        <span class="rail-dot" aria-hidden="true">
          <IconCheck v-if="idx < currentIdx" class="w-3.5 h-3.5" :stroke-width="2.5" />
          <span v-else class="rail-dot-num">{{ idx + 1 }}</span>
        </span>
        <span class="rail-label">{{ item.label }}</span>
      </li>
    </ol>
  </aside>
</template>

<style scoped>
.rail-wide {
  width: 15.5rem;
  flex: 0 0 15.5rem;
  background: var(--bg-inset);
  border-right: 1px solid var(--border);
  padding: 1.5rem 1.125rem;
  display: flex;
  flex-direction: column;
  gap: 1.75rem;
  user-select: none;
}

.rail-brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0 0.25rem;
}

.rail-brand-title {
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.25;
}

.rail-brand-count {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 0.125rem;
}

.rail-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  list-style: none;
  margin: 0;
  padding: 0;
}

.rail-track {
  position: absolute;
  left: 1.125rem;
  top: 1.125rem;
  bottom: 1.125rem;
  width: 2px;
  background: var(--border);
  border-radius: 9999px;
  pointer-events: none;
}

.rail-track-fill {
  width: 100%;
  background: var(--brand);
  border-radius: 9999px;
  transition: height 220ms ease-out;
}

:global(.motion-off) .rail-track-fill {
  transition: none;
}

@media (prefers-reduced-motion: reduce) {
  .rail-track-fill {
    transition: none;
  }
}

.rail-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.375rem 0.625rem 0.375rem 0.25rem;
  border-radius: 0.625rem;
  min-width: 0;
}

.rail-item--current {
  background: var(--brand-glow);
}

.rail-dot {
  position: relative;
  z-index: 1;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 9999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  font-size: 0.75rem;
  font-weight: 700;
  background: var(--bg-inset);
  border: 2px solid var(--border);
  color: var(--text-secondary);
}

.rail-item--done .rail-dot {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
}

.rail-item--current .rail-dot {
  background: var(--bg-surface);
  border-color: var(--brand);
  color: var(--brand-text);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.rail-label {
  font-size: 0.8125rem;
  line-height: 1.3;
  font-weight: 500;
  color: var(--text-secondary);
  overflow-wrap: break-word;
  min-width: 0;
}

.rail-item--done .rail-label {
  color: var(--text-primary);
}

.rail-item--current .rail-label {
  color: var(--brand-text);
  font-weight: 650;
}

/* Compact bar */
.rail-compact {
  background: var(--bg-inset);
  border-bottom: 1px solid var(--border);
  padding: 0.875rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
  flex: 0 0 auto;
}

.rail-compact-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.rail-brand-mini {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.rail-compact-step {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-primary);
}

.rail-segments {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0.375rem;
}

.rail-segment {
  height: 0.3125rem;
  border-radius: 9999px;
  background: var(--border);
}

.rail-segment--done,
.rail-segment--current {
  background: var(--brand);
}

.rail-segment--current {
  box-shadow: 0 0 0 2px var(--brand-glow);
}
</style>
