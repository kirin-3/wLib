<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconCheck, IconMinus } from "@tabler/icons-vue";
import type { OnboardingStepId } from "../../services/api";
import { useOnboarding } from "../../composables/useOnboarding";
import { gamesStore } from "../../composables/useGamesStore";
import { ONBOARDING_STEPS, railStepStates, stepIndex, stepMeta, stepNumber } from "../../utils/onboarding";

const props = defineProps<{
  step: OnboardingStepId;
  compact: boolean;
}>();

const onboarding = useOnboarding();

const currentIdx = computed(() => stepIndex(props.step));
const currentNum = computed(() => stepNumber(props.step));
const currentMeta = computed(() => stepMeta(props.step));
// A passed step shows a check only when it is set. Import with an empty
// library and an unpaired extension show a hollow "not set" marker.
const railItems = computed(() =>
  railStepStates(props.step, {
    importedCount: onboarding.importedCount.value,
    pairedBrowsers: onboarding.pairedBrowsers.value,
    libraryCount: gamesStore.games.value.length,
  }),
);
const progressPercent = computed(() => {
  const spans = ONBOARDING_STEPS.length - 1;
  return spans > 0 ? (currentIdx.value / spans) * 100 : 0;
});
</script>

<template>
  <!-- Compact top bar for narrow windows and compact TV widths -->
  <div v-if="props.compact" class="rail-compact" role="group" aria-label="Setup progress">
    <div class="rail-compact-head">
      <div class="rail-brand-mini">
        <img src="/icon.svg" alt="" class="rail-logo rail-logo--mini" aria-hidden="true" />
        <span class="rail-compact-step">
          Step {{ currentNum }} of {{ ONBOARDING_STEPS.length }} · {{ currentMeta.label }}
        </span>
      </div>
    </div>
    <ol class="rail-segments">
      <li
        v-for="item in railItems"
        :key="item.id"
        class="rail-segment"
        :class="`rail-segment--${item.state}`"
        :data-rail-step="item.id"
        :data-rail-state="item.state"
        :aria-current="item.state === 'current' ? 'step' : undefined"
      >
        <span class="sr-only">{{ item.srLabel }}</span>
      </li>
    </ol>
  </div>

  <!-- Wide vertical step rail -->
  <aside v-else class="rail-wide" aria-label="Setup steps">
    <div class="rail-brand">
      <img src="/icon.svg" alt="" class="rail-logo" aria-hidden="true" />
      <div class="min-w-0">
        <p class="rail-brand-title">wLib setup</p>
        <p class="rail-brand-count">
          Step {{ currentNum }} of {{ ONBOARDING_STEPS.length }}
        </p>
      </div>
    </div>

    <div class="rail-steps">
      <div class="rail-track" aria-hidden="true">
        <div class="rail-track-fill" :style="{ height: `${progressPercent}%` }" />
      </div>
      <ol class="rail-list">
        <li
          v-for="(item, idx) in railItems"
          :key="item.id"
          class="rail-item"
          :class="`rail-item--${item.state}`"
          :data-rail-step="item.id"
          :data-rail-state="item.state"
          :aria-current="item.state === 'current' ? 'step' : undefined"
        >
          <span class="rail-dot" aria-hidden="true">
            <IconCheck v-if="item.state === 'done'" class="w-3.5 h-3.5" :stroke-width="2.5" />
            <IconMinus v-else-if="item.state === 'not-set'" class="w-3.5 h-3.5" :stroke-width="2.5" />
            <span v-else class="rail-dot-num">{{ idx + 1 }}</span>
          </span>
          <span class="rail-label" aria-hidden="true">{{ item.label }}</span>
          <span class="sr-only">{{ item.srLabel }}</span>
        </li>
      </ol>
    </div>
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

/* The logo is dark ink: a light plate keeps it readable on the dark rail. */
.rail-logo {
  width: 1.75rem;
  height: 1.75rem;
  flex: 0 0 auto;
  padding: 0.125rem;
  border-radius: 0.4375rem;
  background: color-mix(in srgb, var(--brand) 14%, var(--text-primary));
}

.rail-logo--mini {
  width: 1.375rem;
  height: 1.375rem;
  padding: 0.0625rem;
  border-radius: 0.3125rem;
}

.light .rail-logo {
  background: color-mix(in srgb, var(--brand) 14%, var(--bg-surface));
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

.rail-steps {
  position: relative;
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

.motion-off .rail-track-fill {
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

.rail-item--not-set .rail-dot {
  border-style: dashed;
  border-color: var(--border-hover);
  color: var(--text-muted);
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
  list-style: none;
  margin: 0;
  padding: 0;
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

/* Not set: a hollow segment, so it does not read as done or as upcoming. */
.rail-segment--not-set {
  background: transparent;
  box-shadow: inset 0 0 0 1px var(--text-muted);
}

.rail-segment--current {
  box-shadow: 0 0 0 2px var(--brand-glow);
}
</style>
