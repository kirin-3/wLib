<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch, type Component } from "vue";
import { useRouter } from "vue-router";
import { IconArrowLeft, IconArrowRight, IconCheck } from "@tabler/icons-vue";
import type { OnboardingStepId } from "../../services/api";
import { useOnboarding } from "../../composables/useOnboarding";
import { stepAnnouncement } from "../../utils/onboarding";
import { motionEnabled } from "../../utils/motionPreference";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import { useControllerActions, type ControllerHint } from "../../utils/controllerActions";
import { useWindowCssWidth } from "../../utils/windowWidth";
import { inputModality } from "../../utils/inputModality";
import { tvModeActive } from "../../utils/tvMode";
import OnboardingStepRail from "./OnboardingStepRail.vue";
import WelcomeStep from "./WelcomeStep.vue";
import CatalogStep from "./CatalogStep.vue";
import LewdCornerStep from "./LewdCornerStep.vue";
import ImportStep from "./ImportStep.vue";
import ExtensionStep from "./ExtensionStep.vue";
import DoneStep from "./DoneStep.vue";

const onboarding = useOnboarding();
const router = useRouter();
const panelRef = useTemplateRef<HTMLElement>("panelRef");
const bodyRef = useTemplateRef<HTMLElement>("bodyRef");
const windowCssWidth = useWindowCssWidth();

// 56rem = 896px at default 16px root font-size; measure against CSS width so
// interface scale and TV mode collapse cleanly to the compact top bar.
const compactRail = computed(() => {
  const rootSize =
    typeof document !== "undefined"
      ? parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
      : 16;
  const thresholdPx = 56 * rootSize;
  return windowCssWidth.value < thresholdPx || (tvModeActive.value && windowCssWidth.value < 64 * rootSize);
});

const hintsBarVisible = computed(
  () => tvModeActive.value || inputModality.value === "controller",
);

const stepComponents: Record<OnboardingStepId, Component> = {
  welcome: WelcomeStep,
  catalog: CatalogStep,
  lc: LewdCornerStep,
  import: ImportStep,
  extension: ExtensionStep,
  done: DoneStep,
};

const currentComponent = computed(() => stepComponents[onboarding.step.value]);
const isWelcome = computed(() => onboarding.step.value === "welcome");
const isDone = computed(() => onboarding.step.value === "done");
const showFooter = computed(() => !isWelcome.value);

const transitionName = computed(() => {
  if (!motionEnabled.value) return "";
  return onboarding.direction.value === "backward"
    ? "onboarding-slide-back"
    : "onboarding-slide-forward";
});

const liveAnnouncement = ref("");

const focusStepHeading = async () => {
  await nextTick();
  bodyRef.value?.scrollTo({ top: 0, behavior: "instant" });
  const heading = panelRef.value?.querySelector<HTMLElement>("#onboarding-step-title");
  heading?.focus();
};

const handleBack = () => {
  if (onboarding.busy.value) return;
  void onboarding.back();
};

const handlePrimary = async () => {
  if (onboarding.busy.value) return;
  if (isDone.value) {
    const mode = onboarding.mode.value;
    await onboarding.finish();
    if (mode === "first-run" || router.currentRoute.value.path !== "/") {
      void router.push("/");
    }
    return;
  }
  await onboarding.next();
};

const handleSkip = async () => {
  if (onboarding.busy.value) return;
  const mode = onboarding.mode.value;
  await onboarding.skip();
  if (mode === "first-run" && router.currentRoute.value.path !== "/") {
    void router.push("/");
  }
};

const selectOpen = (): boolean =>
  typeof document !== "undefined" && !!document.querySelector("[data-controller-select]");

useModalKeyboard(
  panelRef,
  () => onboarding.open.value,
  () => {
    if (!onboarding.busy.value) void onboarding.back();
  },
);

useControllerActions(panelRef, {
  back: () => {
    if (!onboarding.busy.value) void onboarding.back();
    return true;
  },
  prevTab: () => {
    if (!onboarding.busy.value && !selectOpen()) void onboarding.back();
  },
  nextTab: () => {
    if (!onboarding.busy.value && !selectOpen() && !isDone.value) void onboarding.next();
  },
  hints: (): ControllerHint[] => {
    if (isWelcome.value) {
      return [
        { button: "A", label: "Select" },
        { button: "RB", label: "Next" },
      ];
    }
    if (isDone.value) {
      return [
        { button: "A", label: "Select" },
        { button: "B", label: "Back" },
        { button: "LB", label: "Previous" },
      ];
    }
    return [
      { button: "A", label: "Select" },
      { button: "B", label: "Back" },
      { button: "LB / RB", label: "Step" },
    ];
  },
});

watch(
  () => [onboarding.open.value, onboarding.step.value] as const,
  ([isOpen, step]) => {
    if (!isOpen) return;
    liveAnnouncement.value = stepAnnouncement(step);
    // Focus now; when motion is on, @after-enter focuses the new heading
    // again after the swap completes.
    void focusStepHeading();
  },
  { immediate: true },
);
</script>

<template>
  <div
    v-if="onboarding.open.value"
    class="onboarding-backdrop fixed inset-0 z-[160] flex items-center justify-center p-4"
    :class="{ 'onboarding-backdrop--hints': hintsBarVisible }"
    data-testid="onboarding-overlay"
  >
    <div
      ref="panelRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="onboarding-step-title"
      tabindex="-1"
      class="onboarding-panel"
      :class="{ 'onboarding-panel--compact': compactRail }"
    >
      <div
        class="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="onboarding-announcer"
      >
        {{ liveAnnouncement }}
      </div>

      <OnboardingStepRail :step="onboarding.step.value" :compact="compactRail" />

      <div class="onboarding-main">
        <div ref="bodyRef" class="onboarding-body">
          <Transition
            :name="transitionName"
            mode="out-in"
            @after-enter="focusStepHeading"
          >
            <component :is="currentComponent" :key="onboarding.step.value" />
          </Transition>
        </div>

        <footer v-if="showFooter" class="onboarding-footer">
          <button
            v-if="!isDone"
            type="button"
            class="ui-card-btn ui-card-btn--link onboarding-skip-btn"
            :disabled="onboarding.busy.value"
            @click="handleSkip"
          >
            Skip setup
          </button>
          <span v-else aria-hidden="true" />

          <div class="onboarding-footer-actions">
            <button
              type="button"
              class="ui-card-btn onboarding-back-btn"
              :disabled="onboarding.busy.value"
              @click="handleBack"
            >
              <IconArrowLeft class="w-4 h-4 shrink-0" aria-hidden="true" />
              <span>Back</span>
            </button>

            <button
              type="button"
              class="ui-card-btn ui-card-btn--primary onboarding-primary-btn"
              :disabled="onboarding.busy.value"
              @click="handlePrimary"
            >
              <template v-if="isDone">
                <IconCheck class="w-4 h-4 shrink-0" aria-hidden="true" />
                <span>Go to library</span>
              </template>
              <template v-else>
                <span>Next</span>
                <IconArrowRight class="w-4 h-4 shrink-0" aria-hidden="true" />
              </template>
            </button>
          </div>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.onboarding-backdrop {
  width: var(--app-vw);
  height: var(--app-vh);
  background:
    radial-gradient(ellipse 65% 55% at 10% 8%, var(--brand-glow-strong), transparent 70%),
    radial-gradient(ellipse 60% 55% at 92% 92%, color-mix(in srgb, var(--brand-deep) 35%, transparent), transparent 72%),
    var(--bg-base);
}

.onboarding-backdrop--hints {
  padding-bottom: 3.5rem;
}

.onboarding-panel {
  display: flex;
  flex-direction: row;
  width: min(calc(var(--app-vw) - 2rem), 60rem);
  height: min(calc(var(--app-vh) * 0.9), 44rem);
  max-width: 100%;
  max-height: 100%;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 1rem;
  box-shadow: var(--shadow-modal);
  overflow: hidden;
  outline: none;
}

.onboarding-backdrop--hints .onboarding-panel {
  height: min(calc(var(--app-vh) - 4.75rem), 44rem);
}

.onboarding-panel--compact {
  flex-direction: column;
}

.onboarding-main {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.onboarding-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 1.75rem 2rem;
}

.onboarding-panel--compact .onboarding-body {
  padding: 1.25rem 1.25rem;
}

.onboarding-footer {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.875rem 2rem;
  background: var(--bg-inset);
  border-top: 1px solid var(--border);
}

.onboarding-panel--compact .onboarding-footer {
  padding: 0.75rem 1.25rem;
}

.onboarding-footer-actions {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  margin-left: auto;
}

.onboarding-skip-btn {
  color: var(--text-secondary);
}

.onboarding-back-btn,
.onboarding-primary-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
}

.onboarding-primary-btn {
  min-width: 9.25rem;
}

/* Directional step transitions (transform + opacity only, 220 ms ease-out) */
.onboarding-slide-forward-enter-active,
.onboarding-slide-forward-leave-active,
.onboarding-slide-back-enter-active,
.onboarding-slide-back-leave-active {
  transition: opacity 220ms ease-out, transform 220ms ease-out;
}

.onboarding-slide-forward-enter-from {
  opacity: 0;
  transform: translateX(1.25rem);
}

.onboarding-slide-forward-leave-to {
  opacity: 0;
  transform: translateX(-1.25rem);
}

.onboarding-slide-back-enter-from {
  opacity: 0;
  transform: translateX(-1.25rem);
}

.onboarding-slide-back-leave-to {
  opacity: 0;
  transform: translateX(1.25rem);
}

:global(.motion-off) .onboarding-slide-forward-enter-active,
:global(.motion-off) .onboarding-slide-forward-leave-active,
:global(.motion-off) .onboarding-slide-back-enter-active,
:global(.motion-off) .onboarding-slide-back-leave-active {
  transition: none !important;
}

@media (prefers-reduced-motion: reduce) {
  .onboarding-slide-forward-enter-active,
  .onboarding-slide-forward-leave-active,
  .onboarding-slide-back-enter-active,
  .onboarding-slide-back-leave-active {
    transition: none !important;
  }
}
</style>
