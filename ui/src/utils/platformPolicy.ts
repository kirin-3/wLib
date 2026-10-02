// SPDX-License-Identifier: GPL-3.0-or-later
import type { PlatformCapabilities } from "../services/api";

export const CONSERVATIVE_PLATFORM_CAPABILITIES: PlatformCapabilities = {
  platform: "unsupported",
  native_windows_launch: false,
  wine_proton: false,
  runtime_installers: false,
  wayland: false,
  rpgmaker_linux: false,
  cheat_engine_injection: false,
  launch_modes: ["auto"],
  data_dir: "",
  cache_dir: "",
  playwright_browsers_path: "",
  extension_dir: "",
};

export const conservativePlatformCapabilities = (): PlatformCapabilities => ({
  ...CONSERVATIVE_PLATFORM_CAPABILITIES,
  launch_modes: [...CONSERVATIVE_PLATFORM_CAPABILITIES.launch_modes],
});
