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
