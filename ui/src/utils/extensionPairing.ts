// SPDX-License-Identifier: GPL-3.0-or-later
import type { ExtensionPairingEntry, ExtensionPairingResponse } from "../services/api";
import type { ConfirmOptions } from "./confirm";

export const EXTENSION_PAIRING_EVENT = "wlib-extension-pairing";

export const pairingQuestion = (request: ExtensionPairingEntry): ConfirmOptions => ({
  title: "Allow browser extension?",
  message:
    `A ${request.browser} browser extension asks to connect to wLib. Its code is ${request.id}. ` +
    "Allow it only if you installed or updated the wLib extension and its options page shows the same code. " +
    "An allowed extension can read and change your library.",
  confirmLabel: "Allow",
  cancelLabel: "Refuse",
});

interface PairingApi {
  getExtensionPairing(): Promise<ExtensionPairingResponse>;
  resolveExtensionPairing(id: string, allow: boolean): Promise<{ success?: boolean }>;
}

const asked = new Set<string>();

/** Ask the user about every pending request once; later calls skip ones already asked. */
export const answerPairingRequests = async (
  api: PairingApi,
  confirm: (options: ConfirmOptions) => Promise<boolean>,
): Promise<void> => {
  const state = await api.getExtensionPairing();
  for (const request of state.pending || []) {
    if (asked.has(request.id)) continue;
    asked.add(request.id);
    const allow = await confirm(pairingQuestion(request));
    await api.resolveExtensionPairing(request.id, allow);
  }
};
