// SPDX-License-Identifier: GPL-3.0-or-later
// Discover's Download adds a catalog game as wishlisted, then opens the build
// picker. When the picker closes for that game, this asks the backend to
// remove it again; the backend keeps it when a download was queued, when it
// was not added by Discover in this session, or when it is no longer
// wishlisted. The watch lives outside any component, so leaving Discover while
// the picker is open does not lose it.
import { watch, type Ref, type WatchStopHandle } from "vue";
import { api } from "../services/api";
import { discardAfterPickerClose } from "../utils/discoverActions";

let pending: number | null = null;
let stop: WatchStopHandle | null = null;

export function discardWhenPickerCloses(
  gameId: number,
  selectedGame: Ref<{ id: number } | null>,
  onRemoved: () => void,
): void {
  stop?.();
  pending = gameId;
  stop = watch(selectedGame, (next, previous) => {
    const id = discardAfterPickerClose(pending, previous, next);
    if (id === null) return;
    stop?.();
    stop = null;
    pending = null;
    void api.discardDiscoverGame(id).then((result) => {
      if (result.success && result.removed) onRemoved();
    });
  });
}
