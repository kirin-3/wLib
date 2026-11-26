// SPDX-License-Identifier: GPL-3.0-or-later
import { api } from "../services/api";
import { createOnboardingController } from "../utils/onboarding";

export {
  createOnboardingController,
  type OnboardingApiClient,
  type OnboardingDirection,
  type OnboardingMode,
  type PendingExtensionRunner,
} from "../utils/onboarding";

const singleton = createOnboardingController(api);

export function useOnboarding() {
  return singleton;
}
