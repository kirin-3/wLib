<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import {
  IconArrowRight,
  IconBook2,
  IconFileImport,
  IconPuzzle,
  IconWorld,
} from "@tabler/icons-vue";
import { useRouter } from "vue-router";
import { useOnboarding } from "../../composables/useOnboarding";

const onboarding = useOnboarding();
const router = useRouter();

const handleSkip = async () => {
  const mode = onboarding.mode.value;
  await onboarding.skip();
  if (mode === "first-run" && router.currentRoute.value.path !== "/") {
    void router.push("/");
  }
};

const previewChips = [
  { id: "catalog", label: "Game catalog", icon: IconBook2 },
  { id: "lc", label: "LewdCorner", icon: IconWorld },
  { id: "import", label: "Import", icon: IconFileImport },
  { id: "extension", label: "Extension", icon: IconPuzzle },
];
</script>

<template>
  <div class="welcome-step">
    <div class="welcome-hero">
      <div class="welcome-logo-wrap" aria-hidden="true">
        <img src="/icon.svg" alt="" class="welcome-logo" />
      </div>

      <h2 id="onboarding-step-title" tabindex="-1" class="welcome-title">
        Welcome to wLib
      </h2>

      <p class="welcome-lead">
        Take a minute to choose how wLib handles the game catalog, LewdCorner,
        imports from other apps, and the browser extension.
      </p>

      <ul class="welcome-chips" aria-label="What setup covers">
        <li v-for="chip in previewChips" :key="chip.id" class="welcome-chip">
          <component :is="chip.icon" class="welcome-chip-icon" aria-hidden="true" />
          <span>{{ chip.label }}</span>
        </li>
      </ul>

      <div class="welcome-actions">
        <button
          type="button"
          class="ui-card-btn ui-card-btn--primary welcome-start-btn"
          data-controller-primary
          :disabled="onboarding.busy.value"
          @click="onboarding.next()"
        >
          <span>Get started</span>
          <IconArrowRight class="w-4 h-4 shrink-0" aria-hidden="true" />
        </button>

        <button
          type="button"
          class="ui-card-btn ui-card-btn--link welcome-skip-btn"
          :disabled="onboarding.busy.value"
          @click="handleSkip"
        >
          Skip setup
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.welcome-step {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem 0.5rem;
}

.welcome-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 34rem;
  width: 100%;
}

.welcome-logo-wrap {
  width: 6.25rem;
  height: 6.25rem;
  border-radius: 1.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  /* The logo is dark ink, so the tile stays light in both themes. */
  background: radial-gradient(
    circle at 50% 35%,
    color-mix(in srgb, var(--brand) 14%, var(--text-primary)),
    color-mix(in srgb, var(--brand) 26%, var(--text-secondary))
  );
  border: 1px solid color-mix(in srgb, var(--brand) 34%, var(--border));
  box-shadow: var(--shadow-brand);
  margin-bottom: 1.5rem;
  animation: welcome-icon-in 300ms ease-out both;
}

.light .welcome-logo-wrap {
  background: radial-gradient(
    circle at 50% 35%,
    color-mix(in srgb, var(--brand) 22%, var(--bg-raised)),
    var(--bg-inset)
  );
}

.welcome-logo {
  width: 5rem;
  height: 5rem;
  display: block;
}

@keyframes welcome-icon-in {
  from {
    opacity: 0;
    transform: scale(0.92);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.motion-off .welcome-logo-wrap {
  animation: none;
}

@media (prefers-reduced-motion: reduce) {
  .welcome-logo-wrap {
    animation: none;
  }
}

.welcome-title {
  font-size: 1.75rem;
  line-height: 1.2;
  font-weight: 750;
  letter-spacing: -0.02em;
  color: var(--text-primary);
  text-wrap: balance;
  outline: none;
}

.welcome-lead {
  margin-top: 0.75rem;
  font-size: 0.9375rem;
  line-height: 1.55;
  color: var(--text-secondary);
  text-wrap: pretty;
  max-width: 32rem;
}

.welcome-chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.5rem;
  list-style: none;
  margin: 1.5rem 0 0;
  padding: 0;
}

.welcome-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.375rem 0.75rem;
  border-radius: 9999px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
}

.welcome-chip-icon {
  width: 0.95rem;
  height: 0.95rem;
  color: var(--brand-text);
  flex: 0 0 auto;
}

.welcome-actions {
  margin-top: 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.welcome-start-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-width: 12rem;
  padding: 0.65rem 1.5rem;
  font-size: 0.9375rem;
  font-weight: 650;
}

.welcome-skip-btn {
  font-size: 0.8125rem;
  color: var(--text-secondary);
}
</style>
