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
  if (pendingCapabilities) return pendingCapabilities;

  pendingCapabilities = api
    .getPlatformCapabilities()
    .then((capabilities) => {
      cachedCapabilities = capabilities;
      return capabilities;
    })
    .catch((error: unknown) => {
      console.error("Failed to load platform capabilities", error);
      cachedCapabilities = conservativePlatformCapabilities();
      return cachedCapabilities;
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
