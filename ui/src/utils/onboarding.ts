// SPDX-License-Identifier: GPL-3.0-or-later
import { ref, shallowRef } from "vue";
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

export interface OnboardingStepMeta {
  id: OnboardingStepId;
  label: string;
}

export const ONBOARDING_STEPS: readonly OnboardingStepMeta[] = [
  { id: "welcome", label: "Welcome" },
  { id: "catalog", label: "Game catalog" },
  { id: "lc", label: "LewdCorner" },
  { id: "import", label: "Import from other apps" },
  { id: "extension", label: "Browser extension" },
  { id: "done", label: "Done" },
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

export interface OnboardingProgressInput {
  /** Games imported in this run of setup. Resets on reload. */
  importedCount: number;
  pairedBrowsers: readonly string[];
  /**
   * Games in the library now. It survives a restart, so an import made before
   * the app restarted still counts, and a library that already has games
   * needs no import.
   */
  libraryCount?: number;
}

export interface OnboardingSummaryInput extends OnboardingProgressInput {
  catalogEnabled: boolean;
  lcEnabled: boolean;
  /** Steam Gaming Mode: no desktop browser, so no "Pair now" action. */
  gamingMode?: boolean;
}

function importedTotal(input: OnboardingProgressInput): number {
  return Math.max(0, Math.floor(input.importedCount || 0));
}

function libraryTotal(input: OnboardingProgressInput): number {
  return Math.max(0, Math.floor(input.libraryCount || 0));
}

function pairedList(input: OnboardingProgressInput): string[] {
  return [...new Set(input.pairedBrowsers.map((b) => b.trim()).filter(Boolean))];
}

/**
 * True when a step the user has passed is set. Welcome, catalog and LewdCorner
 * are always set: On and Off are both a choice. Import needs an imported game
 * or a library that has games. The extension needs a paired browser.
 */
export function stepIsSet(step: OnboardingStepId, input: OnboardingProgressInput): boolean {
  if (step === "import") return importedTotal(input) > 0 || libraryTotal(input) > 0;
  if (step === "extension") return pairedList(input).length > 0;
  return true;
}

export type OnboardingRailState = "done" | "not-set" | "current" | "upcoming";

export interface OnboardingRailItem {
  id: OnboardingStepId;
  label: string;
  state: OnboardingRailState;
  /** Screen reader name, for example "Import from other apps, not set". */
  srLabel: string;
}

export function railStepStates(
  current: OnboardingStepId,
  input: OnboardingProgressInput,
): OnboardingRailItem[] {
  const currentIdx = stepIndex(current);
  return ONBOARDING_STEPS.map((meta, idx) => {
    let state: OnboardingRailState;
    if (idx === currentIdx) state = "current";
    else if (idx > currentIdx) state = "upcoming";
    else state = stepIsSet(meta.id, input) ? "done" : "not-set";
    // The current step needs no suffix: aria-current already names it.
    const suffix =
      state === "done" ? ", done" : state === "not-set" ? ", not set" : state === "upcoming" ? ", not started" : "";
    return { id: meta.id, label: meta.label, state, srLabel: `${meta.label}${suffix}` };
  });
}

export interface OnboardingDoneHeadline {
  allSet: boolean;
  /** Steps that are not set (import, extension). */
  leftCount: number;
  title: string;
  /** Short line under the title. Empty when everything is set. */
  leftText: string;
  /** The sentence under that line. */
  lead: string;
}

export function leftForLaterText(count: number): string {
  if (count <= 0) return "";
  return count === 1 ? "1 thing is left for later." : `${count} things are left for later.`;
}

export function doneHeadline(input: OnboardingProgressInput): OnboardingDoneHeadline {
  const leftCount = (["import", "extension"] as const).filter((id) => !stepIsSet(id, input)).length;
  const allSet = leftCount === 0;
  return {
    allSet,
    leftCount,
    title: allSet ? "You're all set" : "Setup is finished",
    leftText: leftForLaterText(leftCount),
    lead: allSet
      ? "Here is a summary of your setup. You can change any of these choices at any time in Settings."
      : `You can set ${leftCount === 1 ? "it" : "them"} up now or at a later time.`,
  };
}

export type OnboardingSummaryTone = "brand" | "success" | "muted";

export interface OnboardingSummaryAction {
  step: OnboardingStepId;
  label: string;
}

export type OnboardingSummaryIconKey = "book" | "world" | "import" | "puzzle";

export interface OnboardingSummaryRow {
  id: "catalog" | "lc" | "import" | "extension";
  icon: OnboardingSummaryIconKey;
  label: string;
  value: string;
  tone: OnboardingSummaryTone;
  /** False when the step is left for later. */
  set: boolean;
  /** A button that goes back to the step, for a row that is not set. */
  action: OnboardingSummaryAction | null;
  /** Text in place of the action when the action is not possible here. */
  note: string | null;
}

export function buildDoneSummary(input: OnboardingSummaryInput): OnboardingSummaryRow[] {
  const browsers = pairedList(input);
  const imported = importedTotal(input);
  const library = libraryTotal(input);
  const paired = browsers.length > 0;
  const importSet = stepIsSet("import", input);
  // This run's import count when there is one. Else the library size, which
  // stays true after a restart (the run count resets to 0).
  const importValue =
    imported > 0
      ? `${imported} ${imported === 1 ? "game" : "games"} imported`
      : library > 0
        ? `${library} ${library === 1 ? "game" : "games"} in your library`
        : "None imported";
  return [
    {
      id: "catalog",
      icon: "book",
      label: "Game catalog",
      value: input.catalogEnabled ? "On" : "Off",
      tone: input.catalogEnabled ? "brand" : "muted",
      set: true,
      action: null,
      note: null,
    },
    {
      id: "lc",
      icon: "world",
      label: "LewdCorner",
      value: input.lcEnabled ? "On" : "Off",
      tone: input.lcEnabled ? "brand" : "muted",
      set: true,
      action: null,
      note: null,
    },
    {
      id: "import",
      icon: "import",
      label: "Import from other apps",
      value: importValue,
      tone: importSet ? "success" : "muted",
      set: importSet,
      action: importSet ? null : { step: "import", label: "Import games" },
      note: null,
    },
    {
      id: "extension",
      icon: "puzzle",
      label: "Browser extension",
      value: paired ? `Paired with ${browsers.join(", ")}` : "Not paired",
      tone: paired ? "success" : "muted",
      set: paired,
      action: paired || input.gamingMode ? null : { step: "extension", label: "Pair now" },
      note: !paired && input.gamingMode ? "Later, in Desktop Mode" : null,
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

// An inspected import preview that the import step has not run yet. While it
// is set, the footer primary and RB run the import instead of moving on, so
// Next can never drop the preview without the user saying so.
export interface PendingImport {
  /** Games the import will add or update (new plus matched). */
  count: number;
  /** Runs the import. Resolves true when it succeeded. */
  run: () => Promise<boolean>;
}

export function importActionLabel(count: number): string {
  return count === 1 ? "Import 1 game" : `Import ${count} games`;
}

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
  const pendingImport = shallowRef<PendingImport | null>(null);

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
    pendingImport.value = null;
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
    pendingImport.value = null;
    open.value = true;
  };

  // True while Next waits for the catalog hold release. A second Next, Back,
  // Skip or Done in that time (double click, held RB) would skip a step.
  let moving = false;

  const importWaiting = (): boolean =>
    step.value === "import" && pendingImport.value !== null;

  const next = async (): Promise<boolean> => {
    // A waiting import preview blocks a plain Next. Use advance() to run it
    // or skipImport() to drop it.
    if (busy.value || moving || importWaiting()) return false;
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
    if (step.value === "import") pendingImport.value = null;
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

  // The footer primary and RB: run a waiting import, else go to the next step.
  // After the import the step stays open, so the user sees the result.
  const advance = async (): Promise<boolean> => {
    if (busy.value || moving) return false;
    const pending = importWaiting() ? pendingImport.value : null;
    if (pending) {
      try {
        return await pending.run();
      } catch (error) {
        console.error("Could not run the import", error);
        return false;
      }
    }
    return next();
  };

  const skipImport = async (): Promise<boolean> => {
    if (busy.value || moving || step.value !== "import") return false;
    pendingImport.value = null;
    return next();
  };

  // Jump back to an earlier step, for example from a Done row that is not
  // set. Only backward: a forward jump would pass the catalog hold release.
  // After the jump, Next walks forward step by step as usual.
  const goTo = async (target: OnboardingStepId): Promise<boolean> => {
    if (busy.value || moving || !open.value) return false;
    if (stepIndex(target) >= stepIndex(step.value)) return false;

    direction.value = "backward";
    if (step.value === "import") pendingImport.value = null;
    step.value = target;

    if (mode.value === "first-run") {
      try {
        await client.setOnboardingStep(target);
      } catch (error) {
        console.error("Could not save onboarding step", error);
      }
    }
    return true;
  };

  const setPendingImport = (value: PendingImport | null): void => {
    pendingImport.value = value;
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
    pendingImport.value = null;
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
    pendingImport,
    pendingExtensionRequests,
    start,
    replay,
    next,
    advance,
    skipImport,
    setPendingImport,
    back,
    goTo,
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

export interface CatalogStepCopy {
  /** Help text under the catalog switch: what On and Off do, said before the user acts. */
  help: string;
  /** The left card: what the catalog gives when on, or what is missing when off. */
  heading: string;
  tone: "brand" | "muted";
  items: readonly string[];
}

const CATALOG_ON_ITEMS = [
  "Browse and search games in Discover",
  "Automatic game details, covers, tags, and F95 ratings",
  "Matching LewdCorner threads when LewdCorner is enabled",
  "Linked DLsite, VNDB, Steam, and Ryuugames pages",
] as const;

const CATALOG_OFF_ITEMS = [
  "No games to browse in Discover",
  "No catalog details, covers, tags, or F95 ratings",
  "No automatic LewdCorner thread matching",
  "No automatic DLsite, VNDB, Steam, or Ryuugames links",
] as const;

/**
 * Copy for the onboarding catalog step. Turning the catalog off deletes
 * catalog.db and discover-index.db, so the help names the deletion only when a
 * local copy exists (a new install holds the download until the user leaves
 * the step, so usually there is none yet).
 */
export function catalogStepCopy(enabled: boolean, installed: boolean): CatalogStepCopy {
  const off = installed ? "When off, wLib deletes its local copy." : "When off, wLib does not download it.";
  return {
    help: `When on, wLib checks for a new catalog at most twice a day. ${off} You can change this in Settings → Sources.`,
    heading: enabled ? "What you get" : "Without the catalog",
    tone: enabled ? "brand" : "muted",
    items: enabled ? CATALOG_ON_ITEMS : CATALOG_OFF_ITEMS,
  };
}
