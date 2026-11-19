// SPDX-License-Identifier: GPL-3.0-or-later
import {
  api,
  type PlatformCapabilities,
} from "../services/api";
import { conservativePlatformCapabilities } from "./platformPolicy";

let cachedCapabilities: PlatformCapabilities | null = null;
let pendingCapabilities: Promise<PlatformCapabilities> | null = null;

export const loadPlatformCapabilities = async (): Promise<PlatformCapabilities> => {
  if (cachedCapabilities) return cachedCapabilities;
  // Called before pywebview injects its API (window.pywebview.api starts out
  // empty), invoke() returns the browser mock; never cache or share that, or
  // Wine/Proton stay hidden for the whole session. Decide before the call: the
  // mock answer waits on a dynamic import, and the bridge can arrive meanwhile.
  const fromBridge = !!window.pywebview?.api?.get_platform_capabilities;
  if (!fromBridge) {
    return api.getPlatformCapabilities().catch((error: unknown) => {
      console.error("Failed to load platform capabilities", error);
      return conservativePlatformCapabilities();
    });
  }
  if (pendingCapabilities) return pendingCapabilities;

  pendingCapabilities = api
    .getPlatformCapabilities()
    .then((capabilities) => {
      cachedCapabilities = capabilities;
      return capabilities;
    })
    .catch((error: unknown) => {
      console.error("Failed to load platform capabilities", error);
      // Report the conservative fallback without caching it: a single failed
      // read must not pin Wine/Proton and the Linux runtime tools to hidden
      // for the rest of the session.
      return conservativePlatformCapabilities();
    })
    .finally(() => {
      pendingCapabilities = null;
    });
  return pendingCapabilities;
};

export const resetPlatformCapabilitiesForTests = (): void => {
  cachedCapabilities = null;
  pendingCapabilities = null;
};
