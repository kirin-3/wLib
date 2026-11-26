// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";
import type {
  CatalogStatusResponse,
  ExtensionPairingResponse,
  ExternalLibraryImportResponse,
  OnboardingFinishResponse,
  OnboardingFinishStatus,
  OnboardingReleaseHoldResponse,
  OnboardingStateResponse,
  OnboardingStepId,
  OnboardingStepResponse,
  SettingsResponse,
} from "../services/api";

export type OnboardingIconKey =
  | "sparkles"
  | "book"
  | "world"
  | "import"
  | "puzzle"
  | "check";

export interface OnboardingStepMeta {
  id: OnboardingStepId;
  label: string;
  shortLabel: string;
  heading: string;
  icon: OnboardingIconKey;
}

export const ONBOARDING_STEPS: readonly OnboardingStepMeta[] = [
  {
    id: "welcome",
    label: "Welcome",
    shortLabel: "Welcome",
    heading: "Welcome to wLib",
    icon: "sparkles",
  },
  {
    id: "catalog",
    label: "Game catalog",
    shortLabel: "Catalog",
    heading: "Game catalog",
    icon: "book",
  },
  {
    id: "lc",
    label: "LewdCorner",
    shortLabel: "LewdCorner",
    heading: "LewdCorner",
    icon: "world",
  },
  {
    id: "import",
    label: "Import from other apps",
    shortLabel: "Import",
    heading: "Import from other apps",
    icon: "import",
  },
  {
    id: "extension",
    label: "Browser extension",
    shortLabel: "Extension",
    heading: "Browser extension",
    icon: "puzzle",
  },
  {
    id: "done",
    label: "Done",
    shortLabel: "Done",
    heading: "You're all set",
    icon: "check",
  },
] as const;

const STEP_IDS = new Set<string>(ONBOARDING_STEPS.map((step) => step.id));

export function normalizeStep(value: unknown): OnboardingStepId {
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (STEP_IDS.has(normalized)) {
      return normalized as OnboardingStepId;
    }
  }
  return "welcome";
}

export function stepIndex(step: OnboardingStepId): number {
  const index = ONBOARDING_STEPS.findIndex((item) => item.id === step);
  return index >= 0 ? index : 0;
}

export function stepNumber(step: OnboardingStepId): number {
  return stepIndex(step) + 1;
}

export function stepMeta(step: OnboardingStepId): OnboardingStepMeta {
  return ONBOARDING_STEPS[stepIndex(step)] ?? ONBOARDING_STEPS[0]!;
}

export function nextStep(step: OnboardingStepId): OnboardingStepId | null {
  const idx = stepIndex(step);
  return ONBOARDING_STEPS[idx + 1]?.id ?? null;
}

export function prevStep(step: OnboardingStepId): OnboardingStepId | null {
  const idx = stepIndex(step);
  return idx > 0 ? (ONBOARDING_STEPS[idx - 1]?.id ?? null) : null;
}

export function stepAnnouncement(step: OnboardingStepId): string {
  const meta = stepMeta(step);
  return `Step ${stepNumber(step)} of ${ONBOARDING_STEPS.length}, ${meta.label}`;
}

export interface OnboardingSummaryInput {
  catalogEnabled: boolean;
  lcEnabled: boolean;
  importedCount: number;
  pairedBrowsers: readonly string[];
}

export type OnboardingSummaryTone = "brand" | "success" | "muted";

export interface OnboardingSummaryRow {
  id: "catalog" | "lc" | "import" | "extension";
  icon: OnboardingIconKey;
  label: string;
  value: string;
  tone: OnboardingSummaryTone;
}

export function buildDoneSummary(input: OnboardingSummaryInput): OnboardingSummaryRow[] {
  const browsers = [...new Set(input.pairedBrowsers.map((b) => b.trim()).filter(Boolean))];
  const imported = Math.max(0, Math.floor(input.importedCount || 0));
  return [
    {
      id: "catalog",
      icon: "book",
      label: "Game catalog",
      value: input.catalogEnabled ? "On" : "Off",
      tone: input.catalogEnabled ? "brand" : "muted",
    },
    {
      id: "lc",
      icon: "world",
      label: "LewdCorner",
      value: input.lcEnabled ? "On" : "Off",
      tone: input.lcEnabled ? "brand" : "muted",
    },
    {
      id: "import",
      icon: "import",
      label: "Import from other apps",
      value:
        imported > 0
          ? `${imported} ${imported === 1 ? "game" : "games"} imported`
          : "None imported",
      tone: imported > 0 ? "success" : "muted",
    },
    {
      id: "extension",
      icon: "puzzle",
      label: "Browser extension",
      value: browsers.length > 0 ? `Paired with ${browsers.join(", ")}` : "Not paired",
      tone: browsers.length > 0 ? "success" : "muted",
    },
  ];
}

export type OnboardingMode = "first-run" | "replay";
export type OnboardingDirection = "forward" | "backward";

export interface OnboardingApiClient {
  getOnboardingState(): Promise<OnboardingStateResponse>;
  setOnboardingStep(step: OnboardingStepId): Promise<OnboardingStepResponse>;
  finishOnboarding(status: OnboardingFinishStatus): Promise<OnboardingFinishResponse>;
  releaseCatalogHold(): Promise<OnboardingReleaseHoldResponse>;
  getSettings(): Promise<Partial<SettingsResponse>>;
  getCatalogStatus(): Promise<CatalogStatusResponse>;
  getExtensionPairing(): Promise<ExtensionPairingResponse>;
}

export type PendingExtensionRunner = () => void | Promise<void>;

export function createOnboardingController(client: OnboardingApiClient) {
  const open = ref(false);
  const mode = ref<OnboardingMode>("first-run");
  const step = ref<OnboardingStepId>("welcome");
  const direction = ref<OnboardingDirection>("forward");
  const busy = ref(false);

  const catalogEnabled = ref(true);
  const catalogInstalled = ref(false);
  const lcEnabled = ref(false);
  const importedCount = ref(0);
  const pairedBrowsers = ref<string[]>([]);

  const pendingExtensionRequests: PendingExtensionRunner[] = [];

  const hydrateCurrentState = async (): Promise<void> => {
    const [settingsRes, catalogRes, pairingRes] = await Promise.allSettled([
      client.getSettings(),
      client.getCatalogStatus(),
      client.getExtensionPairing(),
    ]);
    if (settingsRes.status === "fulfilled" && settingsRes.value) {
      catalogEnabled.value = settingsRes.value.catalog_enabled !== false;
      lcEnabled.value = settingsRes.value.lc_enabled === true;
    }
    if (catalogRes.status === "fulfilled" && catalogRes.value?.success !== false) {
      if (typeof catalogRes.value.enabled === "boolean") {
        catalogEnabled.value = catalogRes.value.enabled;
      }
      catalogInstalled.value = catalogRes.value.installed === true;
    }
    if (pairingRes.status === "fulfilled" && pairingRes.value?.paired) {
      pairedBrowsers.value = pairingRes.value.paired
        .map((entry) => entry.browser)
        .filter((name): name is string => Boolean(name));
    }
  };

  const refreshPairing = async (): Promise<string[]> => {
    try {
      const response = await client.getExtensionPairing();
      const browsers = (response?.paired || [])
        .map((entry) => entry.browser)
        .filter((name): name is string => Boolean(name));
      pairedBrowsers.value = browsers;
      return browsers;
    } catch {
      return pairedBrowsers.value;
    }
  };

  const enqueueExtensionRequest = (runner: PendingExtensionRunner): void => {
    pendingExtensionRequests.push(runner);
  };

  const drainExtensionRequests = async (): Promise<void> => {
    const queued = pendingExtensionRequests.splice(0, pendingExtensionRequests.length);
    for (const run of queued) {
      try {
        await run();
      } catch (error) {
        console.error("Failed to run queued extension request", error);
      }
    }
  };

  const start = async (initialState?: OnboardingStateResponse): Promise<boolean> => {
    let state = initialState;
    if (!state) {
      try {
        state = await client.getOnboardingState();
      } catch (error) {
        console.error("Could not read onboarding state", error);
        return false;
      }
    }
    if (!state || state.success === false) return false;
    if (state.status === "done" || state.status === "skipped") return false;

    await hydrateCurrentState();
    mode.value = "first-run";
    step.value = normalizeStep(state.step);
    direction.value = "forward";
    busy.value = false;
    importedCount.value = 0;
    open.value = true;
    return true;
  };

  const replay = async (): Promise<void> => {
    await hydrateCurrentState();
    mode.value = "replay";
    step.value = "welcome";
    direction.value = "forward";
    busy.value = false;
    importedCount.value = 0;
    open.value = true;
  };

  // True while Next waits for the catalog hold release. A second Next, Back,
  // Skip or Done in that time (double click, held RB) would skip a step.
  let moving = false;

  const next = async (): Promise<boolean> => {
    if (busy.value || moving) return false;
    const current = step.value;
    const target = nextStep(current);
    if (!target) return false;

    if (current === "catalog" && mode.value === "first-run") {
      moving = true;
      try {
        await client.releaseCatalogHold();
      } catch (error) {
        console.error("Could not release catalog hold", error);
      } finally {
        moving = false;
      }
    }

    direction.value = "forward";
    step.value = target;

    if (mode.value === "first-run") {
      try {
        await client.setOnboardingStep(target);
      } catch (error) {
        console.error("Could not save onboarding step", error);
      }
    }
    if (target === "done") {
      void refreshPairing();
    }
    return true;
  };

  const back = async (): Promise<boolean> => {
    if (busy.value || moving) return false;
    const prev = prevStep(step.value);
    if (!prev) return false;

    direction.value = "backward";
    step.value = prev;

    if (mode.value === "first-run") {
      try {
        await client.setOnboardingStep(prev);
      } catch (error) {
        console.error("Could not save onboarding step", error);
      }
    }
    return true;
  };

  const closeWithStatus = async (status: OnboardingFinishStatus): Promise<boolean> => {
    if (busy.value || moving || !open.value) return false;
    if (mode.value === "first-run") {
      moving = true;
      try {
        await client.finishOnboarding(status);
      } catch (error) {
        console.error("Could not finish onboarding", error);
      } finally {
        moving = false;
      }
    }
    open.value = false;
    busy.value = false;
    await drainExtensionRequests();
    return true;
  };

  const skip = (): Promise<boolean> => closeWithStatus("skipped");
  const finish = (): Promise<boolean> => closeWithStatus("done");

  const setBusy = (value: boolean): void => {
    busy.value = value;
  };

  const recordImport = (result: ExternalLibraryImportResponse): void => {
    importedCount.value += (result.created ?? 0) + (result.updated ?? 0);
  };

  const setCatalogState = (enabled: boolean, installed?: boolean): void => {
    catalogEnabled.value = enabled;
    if (typeof installed === "boolean") {
      catalogInstalled.value = installed;
    } else if (!enabled) {
      catalogInstalled.value = false;
    }
  };

  const setLcEnabled = (enabled: boolean): void => {
    lcEnabled.value = enabled;
  };

  const setPairedBrowsers = (browsers: string[]): void => {
    pairedBrowsers.value = browsers;
  };

  return {
    open,
    mode,
    step,
    direction,
    busy,
    catalogEnabled,
    catalogInstalled,
    lcEnabled,
    importedCount,
    pairedBrowsers,
    pendingExtensionRequests,
    start,
    replay,
    next,
    back,
    skip,
    finish,
    setBusy,
    recordImport,
    setCatalogState,
    setLcEnabled,
    setPairedBrowsers,
    refreshPairing,
    enqueueExtensionRequest,
    drainExtensionRequests,
  };
}
