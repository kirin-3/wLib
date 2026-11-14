// SPDX-License-Identifier: GPL-3.0-or-later
import type { UpdateBuild, UpdateMirror } from "../services/api";

export type HostPlatform = "linux" | "windows";
export const inAppMirror = (mirror: UpdateMirror): boolean =>
  !mirror.split && mirror.in_app !== false && ["pixeldrain", "mega", "buzzheavier"].includes(mirror.host.toLowerCase());
export const suitable = (build: UpdateBuild, host: HostPlatform): boolean =>
  (host === "linux" ? ["linux", "windows/linux", "windows", "all", "unknown"] : ["windows", "windows/linux", "all", "unknown"]).includes(build.platform);
export function orderBuilds(builds: UpdateBuild[], host: HostPlatform): { suitable: UpdateBuild[]; other: UpdateBuild[] } {
  const order = host === "linux" ? ["linux", "windows/linux", "windows", "all", "unknown"] : ["windows", "windows/linux", "all", "unknown"];
  return {
    suitable: builds.filter(build => suitable(build, host)).slice().sort((a,b) => order.indexOf(a.platform) - order.indexOf(b.platform)),
    other: builds.filter(build => !suitable(build, host)),
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
  const usable = orderBuilds(builds, host).suitable;
  if (!builds.length) return { kind: "needs-choice", reason: "No builds were found in the thread" };
  if (!usable.length) return { kind: "needs-choice", reason: "No build for this platform was found" };
  if (!usable.some(build => build.kind === "full")) return { kind: "needs-choice", reason: "Only patch builds were found" };
  return { kind: "needs-choice", reason: "No full build has an in-app mirror" };
}
export function downloadFailureMessage(error: string): string {
  const lower = error.toLowerCase();
  // Most specific first: several messages also mention the browser or extension.
  if (lower.includes("disk space")) return `${error}. Free up space and retry.`;
  if (lower.includes("could not save the download")) return `${error}. Check the wLib data folder and retry.`;
  if (lower.includes("quota")) return `${error}. Wait for the host's quota to reset or try another mirror.`;
  if (lower.includes("permission") || lower.includes("buzzheavier downloads")) return `${error}. Turn on Buzzheavier downloads in the wLib extension settings.`;
  if (lower.includes("sign in") || lower.includes("login")) return `${error}. Sign in to F95Zone in your browser and retry.`;
  if (lower.includes("challenge") || lower.includes("timed out")) return `${error}.`;
  if (lower.includes("unsupported") || lower.includes("open in browser") || lower.includes("split archive")) return `${error}. Use Open in browser for this mirror.`;
  if (lower.includes("extension") || lower.includes("open a browser")) return `${error}. Open a browser with the wLib extension and retry.`;
  return `${error}. Try another mirror or open this one in your browser.`;
}
