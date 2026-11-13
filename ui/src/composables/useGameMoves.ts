// SPDX-License-Identifier: GPL-3.0-or-later
// The background move queue's status, polled by the Library while it is open.
import { onMounted, onUnmounted, ref } from "vue";
import { api, type MoveStatus } from "../services/api";
import { moveStateFor as stateFrom, movePollMs, moveSummaryText, type MoveState } from "../utils/gameFiles";
import { startPoller, type Poller } from "../utils/polling";
import { notify } from "../utils/toast";

export const moveStatus = ref<MoveStatus | null>(null);
let poller: Poller | null = null;
let users = 0;

export async function refreshMoveStatus(): Promise<void> {
  const status = await api.getMoveStatus();
  if (!status || status.success === false) return;
  moveStatus.value = status;
  // The backend sends the summary once, so this toast shows once.
  if (status.summary) {
    const { text, type } = moveSummaryText(status.summary);
    notify(text, type);
  }
}

export const moveStateFor = (gameId: number): MoveState => stateFrom(moveStatus.value, gameId);

export function useGameMoves() {
  onMounted(() => {
    users++;
    // startPoller waits 500 ms while moves run and 5 s otherwise, and slows
    // down while the window is inactive, like the other status polls.
    poller ??= startPoller(refreshMoveStatus, () => movePollMs(moveStatus.value));
    poller.runNow();
  });
  onUnmounted(() => {
    users--;
    if (users <= 0 && poller) {
      poller.stop();
      poller = null;
      users = 0;
    }
  });
  return {
    moveStatus,
    moveStateFor,
    /** Poll at once, for example after a move starts. */
    refresh: () => poller?.runNow(),
    cancel: async (all: boolean) => {
      await api.cancelGameMove(all);
      poller?.runNow();
    },
  };
}
