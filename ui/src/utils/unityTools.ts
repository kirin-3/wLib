// SPDX-License-Identifier: GPL-3.0-or-later
import type { BepInExBuild, UnityToolsState } from "../services/api";

export const unityActions = (state: UnityToolsState, selected: BepInExBuild | null, busy: boolean) => {
  let reason = "";
  if (state.platform === "linux") reason = "BepInEx installation is not supported for Linux-native Unity games yet.";
  else if (!state.build.backend) reason = "The scripting backend could not be detected.";
  else if (!state.build.arch) reason = "The architecture could not be detected.";
  else if (!selected) reason = "Choose a BepInEx version.";
  return {
    install: { enabled: !busy && !reason, message: reason },
    uninstall: { enabled: !busy && state.platform === "windows" && !!state.installed, message: state.installed ? "" : "BepInEx is not installed." },
    restore: { enabled: !busy && state.platform === "windows" && state.has_backup },
  };
};

export const switchWarning = (state: UnityToolsState, selected: BepInExBuild | null): string =>
  state.installed && selected && (state.installed.major !== selected.major || state.installed.backend !== selected.backend)
    ? "Changing BepInEx versions or backend may stop existing plugins from working. Continue?" : "";

export const unityWarnings = (state: UnityToolsState): string[] => state.build.backend === "il2cpp"
  ? ["The first IL2CPP launch can take several minutes.", ...(!state.unity_libs ? ["Unity base libraries are not installed; the first launch needs internet unless they are downloaded during installation. No internet or sandbox mode will prevent setup."] : [])]
  : [];
