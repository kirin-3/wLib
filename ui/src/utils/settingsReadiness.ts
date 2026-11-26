// SPDX-License-Identifier: GPL-3.0-or-later
// The "what do I need" summary at the top of Settings > Running games (Linux).
// It mirrors how the launcher picks the default runner: an empty runner is
// system Wine when Wine is installed, else UMU-Proton (core/umu.py
// resolve_runner). Wine runs Windows games directly; any Proton build runs
// through umu-launcher and needs a prepared Steam Linux Runtime
// (core/launcher.py refuses the launch otherwise). Everything else is optional.

export interface RunnerOption { name: string; path: string }

export interface UmuReadiness {
  installed: boolean;
  /** umu needs Python 3 on the host; false only when the backend says it is missing. */
  python3: boolean;
  runtimeReady: boolean;
  /** A umu download or a runtime preparation is running. */
  busy: boolean;
}

export interface WindowsReadinessInput {
  /** The saved global runner ("" = the default). */
  runner: string;
  /** The runners the backend found; null while they load. "wine" in the list means system Wine. */
  runners: RunnerOption[] | null;
  /** For a typed path: whether it exists. null while unknown or for a named runner. */
  runnerPathOk: boolean | null;
  /** null while the umu status loads. */
  umu: UmuReadiness | null;
}

export type ReadinessTone = "ready" | "action" | "checking";
export type ReadinessTarget = "umu" | "wine-proton";

export interface WindowsReadiness {
  tone: ReadinessTone;
  text: string;
  action: { label: string; target: ReadinessTarget } | null;
  /** true when the default runner is a Proton build, so umu is required; null while unknown. */
  usesProton: boolean | null;
}

const NAMED_RUNNERS = new Set(["wine", "GE-Proton", "UMU-Proton"]);

const basename = (path: string): string => {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || "";
};

/** Same rule as core/umu.py proton_path_value and the launcher's is_proton. */
export function isProtonRunner(runner: string): boolean {
  if (runner === "GE-Proton" || runner === "UMU-Proton") return true;
  return basename(runner).toLowerCase().includes("proton");
}

/** Whether the runner is a typed path that the page should check on disk. */
export function isRunnerPath(runner: string): boolean {
  const value = runner.trim();
  return !!value && !NAMED_RUNNERS.has(value);
}

/** A short name for a runner: its name from the list, else its folder or file name. */
export function runnerLabel(runner: string, runners: RunnerOption[]): string {
  const known = runners.find(option => option.path === runner);
  if (known) return known.name;
  if (runner === "wine") return "System Wine";
  const file = basename(runner);
  // ".../GE-Proton9-20/proton" and ".../wine-ge/bin/wine" name the build by a folder.
  if (file === "proton" || file === "wine" || file === "wine64") {
    const parts = runner.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
    const folder = parts[parts.length - (parts[parts.length - 2] === "bin" ? 3 : 2)];
    if (folder) return folder;
  }
  return file || runner;
}

export function windowsReadiness(input: WindowsReadinessInput): WindowsReadiness {
  const typed = input.runner.trim();
  if (!input.runners) return { tone: "checking", text: "Windows games: checking…", action: null, usesProton: null };
  const systemWine = input.runners.some(option => option.path === "wine");
  const runner = typed || (systemWine ? "wine" : "UMU-Proton");
  const usesProton = isProtonRunner(runner);
  if (isRunnerPath(runner)) {
    if (input.runnerPathOk === null) return { tone: "checking", text: "Windows games: checking…", action: null, usesProton };
    if (!input.runnerPathOk) {
      return { tone: "action", text: "Windows games: the Proton / Wine path was not found.", action: { label: "Check the path", target: "wine-proton" }, usesProton };
    }
  }
  const label = runnerLabel(runner, input.runners);
  // "UMU-Proton" with nothing typed: say why wLib picked Proton.
  const why = !typed && !systemWine ? " Wine is not installed, so wLib uses Proton." : "";
  if (!usesProton) return { tone: "ready", text: `Windows games: ready. They run in ${label}.`, action: null, usesProton };
  const umu = input.umu;
  if (!umu) return { tone: "checking", text: "Windows games: checking…", action: null, usesProton };
  if (umu.busy) return { tone: "checking", text: "Windows games: Proton setup is running…", action: null, usesProton };
  const setUp = { label: "Set up Proton", target: "umu" as const };
  if (!umu.python3) return { tone: "action", text: `Windows games: ${label} needs Python 3 on this computer.${why}`, action: setUp, usesProton };
  if (!umu.installed) return { tone: "action", text: `Windows games: ${label} needs umu-launcher.${why}`, action: setUp, usesProton };
  if (!umu.runtimeReady) return { tone: "action", text: `Windows games: ${label} needs the Steam Linux Runtime.${why}`, action: setUp, usesProton };
  return { tone: "ready", text: `Windows games: ready. They run in ${label} through umu-launcher.`, action: null, usesProton };
}

export interface OptionalTool {
  name: string;
  /** null while its status loads. */
  installed: boolean | null;
}

export interface OptionalToolsSummary {
  tone: "ready" | "checking";
  text: string;
  missing: string[];
}

export function optionalToolsSummary(tools: OptionalTool[]): OptionalToolsSummary {
  if (tools.some(tool => tool.installed === null)) return { tone: "checking", text: "Optional tools: checking…", missing: [] };
  const installed = tools.filter(tool => tool.installed).length;
  const missing = tools.filter(tool => !tool.installed).map(tool => tool.name);
  return { tone: "ready", text: `Optional tools: ${installed} of ${tools.length} installed.`, missing };
}
