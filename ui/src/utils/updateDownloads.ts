// SPDX-License-Identifier: GPL-3.0-or-later
import type { DownloadSite, DownloadSiteOption, GameRecord, UpdateBuild, UpdateMirror } from "../services/api";
import { isWishlisted } from "./libraryGames.ts";

export const selectableBuild = (build: UpdateBuild, wishlisted: boolean): boolean =>
  !wishlisted || build.kind !== "patch";

export function pickerTargetVersion(game: Pick<GameRecord, "latest_version" | "version" | "exe_path">): string {
  return game.latest_version?.trim() || (isWishlisted(game) ? game.version?.trim() || "" : "");
}

/** A site the picker asked for that the game no longer offers (for example LC after "Use LewdCorner" is turned off). */
export const siteUnavailable = (requested: DownloadSite | "", sites: DownloadSiteOption[] | undefined): boolean =>
  !!requested && !!sites?.length && !sites.some(option => option.site === requested);

export type HostPlatform = "linux" | "windows";
export const inAppMirror = (mirror: UpdateMirror): boolean =>
  !mirror.split && mirror.in_app !== false && ["pixeldrain", "mega", "buzzheavier"].includes(mirror.host.toLowerCase());
export const suitable = (build: UpdateBuild, host: HostPlatform): boolean =>
  (host === "linux" ? ["linux", "windows/linux", "windows", "all", "unknown"] : ["windows", "windows/linux", "all", "unknown"]).includes(build.platform);
export function platformLabel(value: string): string {
  if (value === "unknown") return "Any platform";
  if (value === "all") return "All platforms";
  if (value === "ios") return "iOS";
  return value.split("/").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" / ");
}
export const visibleArchivePassword = (password: string | null | undefined): string =>
  (password ?? "").trim();
/** Old builds (from an older-version spoiler) are kept apart from the others, whatever their platform. */
export function orderBuilds(builds: UpdateBuild[], host: HostPlatform): { suitable: UpdateBuild[]; other: UpdateBuild[]; old: UpdateBuild[] } {
  const order = host === "linux" ? ["linux", "windows/linux", "windows", "all", "unknown"] : ["windows", "windows/linux", "all", "unknown"];
  const current = builds.filter(build => build.kind !== "old");
  return {
    suitable: current.filter(build => suitable(build, host)).slice().sort((a,b) => order.indexOf(a.platform) - order.indexOf(b.platform)),
    other: current.filter(build => !suitable(build, host)),
    old: builds.filter(build => build.kind === "old"),
  };
}
export function preselect(builds: UpdateBuild[], host: HostPlatform): { build: UpdateBuild; mirror: UpdateMirror } | null {
  for (const build of orderBuilds(builds, host).suitable) {
    if (build.kind !== "full") continue;
    const mirror = build.mirrors.filter(inAppMirror).sort((a,b) => ["pixeldrain","mega","buzzheavier"].indexOf(a.host) - ["pixeldrain","mega","buzzheavier"].indexOf(b.host))[0];
    if (mirror) return {build, mirror};
  }
  return null;
}
// Download selected: what happens to one game, given the picker's preselection rule.
export type BulkDownloadDecision =
  | { kind: "start"; build: UpdateBuild; mirror: UpdateMirror }
  | { kind: "needs-choice"; reason: string };
export function bulkDownloadDecision(
  links: { success?: boolean; error?: string; builds?: UpdateBuild[] },
  host: HostPlatform,
): BulkDownloadDecision {
  if (!links.success) return { kind: "needs-choice", reason: `Links did not load: ${links.error || "unknown error"}` };
  const builds = links.builds || [];
  const choice = preselect(builds, host);
  if (choice) return { kind: "start", ...choice };
  const ordered = orderBuilds(builds, host);
  const usable = ordered.suitable;
  if (!builds.length) return { kind: "needs-choice", reason: "No builds were found in the thread" };
  if (!usable.length && ordered.old.some(build => suitable(build, host))) return { kind: "needs-choice", reason: "Only older versions were found" };
  if (!usable.length) return { kind: "needs-choice", reason: "No build for this platform was found" };
  if (!usable.some(build => build.kind === "full")) return { kind: "needs-choice", reason: "Only patch builds were found" };
  return { kind: "needs-choice", reason: "No full build has an in-app mirror" };
}
const LC_DOWNLOAD_SWITCH_HINT = "Turn on LewdCorner downloads in the wLib extension settings";
const BUZZHEAVIER_DOWNLOAD_SWITCH_HINT = "Turn on Buzzheavier downloads in the wLib extension settings.";

export function lcDownloadFailureMessage(error: string, site?: DownloadSite): string | undefined {
  const lower = error.toLowerCase();
  if (lower.includes("lewdcorner check") || (site === "lc" && lower.includes("timed out"))) return "Finish the LewdCorner check in your browser";
  if (lower.includes("lc_login_required") || lower.includes("sign in to lewdcorner")) return "Sign in to LewdCorner in your browser";
  if (lower.includes("lewdcorner downloads")) return LC_DOWNLOAD_SWITCH_HINT;
  if (lower.includes("lc_plus_required") || lower.includes("lc plus thread")) return "This is an LC Plus thread. Use F95Zone or an LC Plus account.";
  if (lower.includes("lewdcorner switch")) return error;
}

export function downloadFailureMessage(error: string, site?: DownloadSite): string {
  const lcMessage = lcDownloadFailureMessage(error, site);
  if (lcMessage) return lcMessage;
  const lower = error.toLowerCase();
  // Most specific first: several messages also mention the browser or extension.
  if (lower.includes("disk space")) return `${error}. Free up space and retry.`;
  if (lower.includes("could not save the download")) return `${error}. Check the wLib data folder and retry.`;
  if (lower.includes("quota")) return `${error}. Wait for the host's quota to reset or try another mirror.`;
  if (lower.includes("permission") || lower.includes("buzzheavier downloads")) return `${error}. ${BUZZHEAVIER_DOWNLOAD_SWITCH_HINT}`;
  if (lower.includes("sign in") || lower.includes("login")) return `${error}. Sign in to F95Zone in your browser and retry.`;
  if (lower.includes("challenge") || lower.includes("timed out")) return `${error}.`;
  if (lower.includes("unsupported") || lower.includes("open in browser") || lower.includes("split archive")) return `${error}. Use Open in browser for this mirror.`;
  if (lower.includes("extension") || lower.includes("open a browser")) return `${error}. Open a browser with the wLib extension and retry.`;
  return `${error}. Try another mirror or open this one in your browser.`;
}

export function needsExtensionSwitch(error: string, code?: string, site?: DownloadSite): boolean {
  if (code === "permission") return true;
  const message = downloadFailureMessage(error, site);
  return message === LC_DOWNLOAD_SWITCH_HINT || message.endsWith(`. ${BUZZHEAVIER_DOWNLOAD_SWITCH_HINT}`);
}

/** What the build picker does once a download start answers. */
export interface PickerStartEffects {
  /** True while the picker that started the download is still open (not closed, not reopened). */
  stillOpen(): boolean;
  refresh(): void;
  close(): void;
  notify(): void;
  showError(message: string): void;
}

/**
 * Settles a picker's download start. A queued download always refreshes the queue
 * and says so; closing the picker or showing a refusal applies only to the picker
 * that started it, so a late answer never closes or marks another game's picker.
 */
export async function settlePickerStart(
  request: Promise<{ success?: boolean | undefined; error?: string | undefined }>,
  effects: PickerStartEffects,
): Promise<void> {
  try {
    const response = await request;
    // On a refusal the picker stays open and shows why.
    if (!response.success) throw new Error(response.error || "Could not start download");
    effects.refresh();
    if (effects.stillOpen()) effects.close();
    effects.notify();
  } catch (e) {
    if (effects.stillOpen()) effects.showError(String(e instanceof Error ? e.message : e));
  }
}

/** The labelled version line under the picker title: "Installed 1.0 → 2.0", or "Version 2.0" for a first download. */
export interface PickerVersionLine {
  label: "Installed" | "Version";
  /** The installed version; empty when the line names one version only. */
  from: string;
  to: string;
}

/**
 * What the picker header says about versions. `threadVersion` is the version
 * the loaded thread gives (empty before the links load); without it the line
 * uses the version the picker searches for.
 */
export function pickerVersionLine(
  game: Pick<GameRecord, "latest_version" | "version" | "exe_path">,
  threadVersion: string,
): PickerVersionLine | null {
  const target = threadVersion.trim() || pickerTargetVersion(game);
  const installed = isWishlisted(game) ? "" : game.version?.trim() || "";
  if (!installed) return target ? { label: "Version", from: "", to: target } : null;
  if (!target || target === installed) return { label: "Installed", from: "", to: installed };
  return { label: "Installed", from: installed, to: target };
}

/** The line under a mirror's name: whether wLib can download it or only the browser can. */
export function mirrorSubtitle(mirror: UpdateMirror, build: UpdateBuild, host: HostPlatform): string {
  if (mirror.split) return `Part ${mirror.part} · browser only`;
  return suitable(build, host) && inAppMirror(mirror) ? "In-app download" : "Browser only";
}
