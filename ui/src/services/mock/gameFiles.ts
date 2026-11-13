// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-preview mocks for uninstall and move (plan_game_uninstall, uninstall_games,
// plan_game_move, start_game_move, get_move_status, cancel_game_move).
import type { GameFilePlanEntry, GameRecord, MoveStatus, MoveSummary, UninstallOptions, UninstallResult } from "../api";
import { isRecord, readMockGames } from "./games";
import { readMockLaunchTargets, writeMockLaunchTargets } from "./launchTargets";
import { readMockSettings } from "./settings";

const dirname = (path: string): string => path.replace(/[/\\][^/\\]*$/, "");
const basename = (path: string): string => path.replace(/[/\\]+$/, "").split(/[/\\]/).pop() || "";
const within = (child: string, parent: string): boolean => child === parent || child.startsWith(`${parent.replace(/\/+$/, "")}/`);

const writeMockGames = (games: GameRecord[]): void => localStorage.setItem("wlib-mock-games", JSON.stringify(games));

// Slow enough for the 500 ms status poll to show progress in the browser preview.
const MOCK_MOVE_MS_PER_GB = 2500;
const mockSize = (gameId: number): number => Math.round((1.5 + (gameId % 5) * 0.7) * 1024 ** 3);

interface MockMoveJob { gameId: number; dest: string; root: string; title: string }

export class MockGameFiles {
  running = new Set<number>();
  queue: MockMoveJob[] = [];
  current: (MockMoveJob & { started: number; total: number }) | null = null;
  totals: MoveSummary & { done: number } = { moved: 0, skipped: 0, cancelled: 0, failed: 0, first_reason: "", done: 0 };
  summary: MoveSummary | null = null;
  constructor(private readonly isRunning: (id: number) => boolean) {}

  plan(ids: number[], overrides: Record<string, string>, dest: string | null): GameFilePlanEntry[] {
    const games = readMockGames();
    return ids.map((id) => {
      const game = games.find((g) => g.id === id);
      const entry: GameFilePlanEntry = { game_id: id, title: game?.title ?? "", root: "", root_source: "", removable: false, reason: "", saves_folder: "", saves_inside_root: false, blocked_reason: "" };
      if (!game) return { ...entry, blocked_reason: "Game was not found" };
      if (!game.exe_path.trim()) return { ...entry, blocked_reason: "This game is not installed." };
      const override = overrides[String(id)];
      const root = override || dirname(game.exe_path);
      entry.root = root;
      entry.root_source = override ? "override" : "exe_dir";
      const libraryFolders = readMockSettings().library_folders;
      if (libraryFolders.includes(root)) entry.reason = "Refusing to remove the library folder.";
      else if (root === "/" || root === "") entry.reason = "Refusing to remove a drive root.";
      else if (!within(game.exe_path, root)) entry.reason = "The old folder no longer contains the game's executable.";
      else if (games.some((other) => other.id !== id && other.exe_path && within(other.exe_path, root))) entry.reason = "The folder contains another library game.";
      entry.removable = !entry.reason;
      if (/rpg/i.test(game.engine ?? "")) { entry.saves_folder = `${root}/www/save`; entry.saves_inside_root = true; }
      else if (/ren.?py/i.test(game.engine ?? "")) entry.saves_folder = `~/.renpy/${game.title.replace(/\W+/g, "")}-1234567890`;
      if (this.isRunning(id)) entry.blocked_reason = dest === null ? "Close the game before uninstalling it." : "Close the game before moving it.";
      else if (this.state(id)) entry.blocked_reason = "This game is being moved. Wait for the move to finish.";
      if (dest !== null) {
        const target = `${dest.replace(/\/+$/, "")}/${basename(root)}`;
        entry.target = target;
        entry.same_device = dest.split("/")[1] === root.split("/")[1];
        entry.size_bytes = mockSize(id);
        if (!dest) entry.move_reason = "The destination folder does not exist.";
        else if (dest === root) entry.move_reason = "The destination is the game's own folder.";
        else if (within(dest, root)) entry.move_reason = "The destination is inside the game's folder.";
        else if (dirname(root) === dest.replace(/\/+$/, "")) entry.move_reason = "The game is already in this folder.";
        else if (games.some((other) => other.exe_path && within(other.exe_path, target))) entry.move_reason = "The target folder already exists.";
        else entry.move_reason = "";
      }
      return entry;
    });
  }

  uninstall(ids: number[], options: Partial<UninstallOptions>): { success: boolean; mock: true; results: UninstallResult[]; uninstalled: number; failed: number; error?: string } {
    const plan = this.plan(ids, options.root_overrides ?? {}, null);
    const results: UninstallResult[] = plan.map((entry) => {
      const error = entry.blocked_reason || (entry.removable ? "" : entry.reason);
      return { game_id: entry.game_id, success: !error, error };
    });
    const done = new Set(results.filter((r) => r.success).map((r) => r.game_id));
    const games = readMockGames();
    writeMockGames(options.remove_from_library
      ? games.filter((g) => !done.has(g.id))
      : games.map((g) => (done.has(g.id) ? { ...g, exe_path: "" } : g)));
    writeMockLaunchTargets(readMockLaunchTargets().filter((t) => !done.has(t.game_id)));
    const failed = results.filter((r) => !r.success);
    return { success: !failed.length, mock: true, results, uninstalled: done.size, failed: failed.length, ...(failed.length ? { error: failed[0]!.error } : {}) };
  }

  state(id: number): "moving" | "waiting" | "" {
    if (this.current?.gameId === id) return "moving";
    return this.queue.some((job) => job.gameId === id) ? "waiting" : "";
  }

  start(ids: number[], dest: string, overrides: Record<string, string>): { success: boolean; mock: true; queued?: number[]; error?: string } {
    if (!dest) return { success: false, mock: true, error: "The destination folder does not exist." };
    if (!this.current && !this.queue.length) { this.totals = { moved: 0, skipped: 0, cancelled: 0, failed: 0, first_reason: "", done: 0 }; this.summary = null; }
    const queued = ids.filter((id) => !this.state(id));
    const games = readMockGames();
    for (const id of queued) {
      const game = games.find((g) => g.id === id);
      this.queue.push({ gameId: id, dest, root: overrides[String(id)] || dirname(game?.exe_path ?? ""), title: game?.title ?? "" });
    }
    this.advance();
    return { success: true, mock: true, queued };
  }

  private finish(status: keyof MoveSummary, reason = ""): void {
    if (status === "first_reason") return;
    this.totals[status] += 1;
    this.totals.done += 1;
    if (reason && !this.totals.first_reason) this.totals.first_reason = reason;
  }

  private advance(): void {
    const now = Date.now();
    for (;;) {
      if (this.current) {
        const elapsed = now - this.current.started;
        if (elapsed < (this.current.total / 1024 ** 3) * MOCK_MOVE_MS_PER_GB) return;
        const job = this.current;
        const target = `${job.dest.replace(/\/+$/, "")}/${basename(job.root)}`;
        writeMockGames(readMockGames().map((g) => (g.id === job.gameId && within(g.exe_path, job.root) ? { ...g, exe_path: target + g.exe_path.slice(job.root.length) } : g)));
        writeMockLaunchTargets(readMockLaunchTargets().map((t) => (t.game_id === job.gameId && within(t.exe_path, job.root) ? { ...t, exe_path: target + t.exe_path.slice(job.root.length) } : t)));
        this.finish("moved");
        this.current = null;
        if (typeof window !== "undefined") window.dispatchEvent(new Event("wlib-refresh-library"));
      }
      const next = this.queue.shift();
      if (!next) {
        if (this.totals.done) {
          const { done: _done, ...summary } = this.totals;
          this.summary = summary;
          this.totals = { moved: 0, skipped: 0, cancelled: 0, failed: 0, first_reason: "", done: 0 };
        }
        return;
      }
      const entry = this.plan([next.gameId], { [String(next.gameId)]: next.root }, next.dest)[0]!;
      const problem = entry.blocked_reason || (entry.removable ? "" : entry.reason) || entry.move_reason || "";
      if (problem) { this.finish("skipped", problem); continue; }
      this.current = { ...next, started: now, total: entry.same_device ? 0 : mockSize(next.gameId) };
    }
  }

  status(): MoveStatus & { mock: true } {
    this.advance();
    const current = this.current;
    const running = !!current || this.queue.length > 0;
    const elapsed = current ? Date.now() - current.started : 0;
    const speed = current && current.total ? 1024 ** 3 / (MOCK_MOVE_MS_PER_GB / 1000) : 0;
    const result: MoveStatus & { mock: true } = {
      success: true,
      mock: true,
      running,
      current: current ? { game_id: current.gameId, title: current.title, bytes_done: Math.min(current.total, Math.round((elapsed / 1000) * speed)), bytes_total: current.total, speed_bps: Math.round(speed) } : null,
      waiting: this.queue.map((job) => job.gameId),
      position: this.totals.done + (current ? 1 : 0),
      total: this.totals.done + (current ? 1 : 0) + this.queue.length,
    };
    if (!running && this.summary) { result.summary = this.summary; this.summary = null; }
    return result;
  }

  cancel(all: boolean): { success: true; mock: true } {
    if (all) { for (const _job of this.queue) this.finish("cancelled"); this.queue = []; }
    if (this.current) { this.finish("cancelled"); this.current = null; }
    this.advance();
    return { success: true, mock: true };
  }

  respond(method: string, args: unknown[]): unknown {
    const ids = Array.isArray(args[0]) ? args[0].map(Number).filter(Number.isInteger) : [];
    switch (method) {
      case "plan_game_uninstall":
        return { success: true, mock: true, games: this.plan(ids, isRecord(args[1]) ? (args[1] as Record<string, string>) : {}, null) };
      case "uninstall_games":
        return this.uninstall(ids, isRecord(args[1]) ? (args[1] as Partial<UninstallOptions>) : {});
      case "plan_game_move":
        return { success: true, mock: true, games: this.plan(ids, isRecord(args[2]) ? (args[2] as Record<string, string>) : {}, String(args[1] ?? "")) };
      case "start_game_move":
        return this.start(ids, String(args[1] ?? ""), isRecord(args[2]) ? (args[2] as Record<string, string>) : {});
      case "get_move_status":
        return this.status();
      default:
        return this.cancel(args[0] === true);
    }
  }
}
