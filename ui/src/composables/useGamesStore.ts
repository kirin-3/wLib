// SPDX-License-Identifier: GPL-3.0-or-later
import { api, type GameRecord } from "../services/api";
import { createGamesStore } from "../utils/gamesStore";
import { settleRemovals } from "../utils/pendingRemovals";
import { settleUninstalls } from "../utils/pendingUninstalls";

/** The library list shared by every view. */
export const gamesStore = createGamesStore<GameRecord>(
  () => api.getGames(),
  (games) => {
    settleRemovals(games.map((game) => game.id));
    settleUninstalls(games);
  },
);
