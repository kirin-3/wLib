// SPDX-License-Identifier: GPL-3.0-or-later
import type {
  RpgmakerLinuxRunnerStatus,
  RpgmakerRunnerInstallStatus,
} from "../services/api";

export interface RpgmakerRunnerUiState {
  showRunnerInstall: boolean;
  showRunnerMissingToolsNote: boolean;
  runnerMissingToolsReason: string;
  mkxpzInstalled: boolean;
  mkxpzPath: string;
  mkxpzStatusText: string;
  showMkxpzInstall: boolean;
  installing: boolean;
  installTarget: "runner" | "mkxpz" | "";
  installDisabled: boolean;
  stageText: string;
  errorText: string;
}

const formatMb = (bytes: number): number => Math.round(bytes / 1024 / 1024);

export const formatRpgmakerInstallStage = (
  installStatus: RpgmakerRunnerInstallStatus | null | undefined,
): string => {
  if (!installStatus || !installStatus.running) return "";
  const base = installStatus.stage || "Installing…";
  if (installStatus.total > 0 && installStatus.done > 0) {
    return `${base} (${formatMb(installStatus.done)} / ${formatMb(installStatus.total)} MB)`;
  }
  return base;
};

export const getRpgmakerRunnerUiState = (
  status: RpgmakerLinuxRunnerStatus,
  installStatus: RpgmakerRunnerInstallStatus | null | undefined,
): RpgmakerRunnerUiState => {
  const supported = Boolean(status.installable?.supported);
  const canInstall = supported && Boolean(status.installable?.can_install);
  const mkxpzInstalled = Boolean(status.mkxpz?.installed);
  const mkxpzPath = status.mkxpz?.path || "";
  const installing = Boolean(installStatus?.running);

  return {
    showRunnerInstall: !status.available && canInstall,
    showRunnerMissingToolsNote: !status.available && supported && !canInstall,
    runnerMissingToolsReason: status.installable?.reason || "",
    mkxpzInstalled,
    mkxpzPath,
    mkxpzStatusText: mkxpzInstalled
      ? `Installed: ${mkxpzPath}`
      : "Not installed (needed for RPG Maker XP, VX, and VX Ace)",
    showMkxpzInstall: status.available && !mkxpzInstalled && supported,
    installing,
    installTarget: installStatus?.target || "",
    installDisabled: installing,
    stageText: formatRpgmakerInstallStage(installStatus),
    errorText: !installing && installStatus?.error ? installStatus.error : "",
  };
};
