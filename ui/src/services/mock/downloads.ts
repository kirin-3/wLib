// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-preview mock of the download queue (get_download_queue and the queue
// commands). Jobs move through the states on each status call, from the time
// that passed: preparing → queued → running → ready. Set the localStorage key
// "wlib-mock-download-demo" to "1" to start with one download in every section.
import type {
  DownloadJobSource,
  DownloadQueueJob,
  DownloadQueueSettings,
  DownloadQueueStatus,
  UpdateDownloadJob,
} from "../api";
import { readMockGames } from "./games";

const MIB = 1024 ** 2;
const HOST_SPEED = 6 * MIB;
const TORRENT_SPEED = 9 * MIB;
const PREPARE_MS = 1500;
const HISTORY = 300;
const RETRY_DELAYS = [30, 120, 600];

interface MockJob extends DownloadQueueJob {
  readyAt?: number;
  startedAt: number;
}

const now = (): number => Date.now() / 1000;
const titleOf = (gameId: number): string => readMockGames().find((game) => game.id === gameId)?.title ?? `Game ${gameId}`;

function makeJob(kind: "host" | "torrent", gameId: number, version: string, source: DownloadJobSource, size: number | null): MockJob {
  const created = now();
  return {
    job_id: `mock-${Math.random().toString(36).slice(2, 10)}`,
    kind, game_id: gameId, title: titleOf(gameId), version, state: "preparing", reason: "", position: 0,
    error_code: "", error_text: "", bytes_done: 0, bytes_total: size, speed: 0, eta: null,
    retries: 0, retry_at: null, retry_in: null, created_at: created, finished_at: null,
    can_continue: kind === "torrent" || source.host !== "mega", source,
    details: { stage: kind === "torrent" ? "fetching" : "resolving", host: source.host ?? "", peers: 0 },
    readyAt: Date.now() + PREPARE_MS, startedAt: 0,
  };
}

export class MockDownloads {
  jobs: MockJob[] = [];
  history: number[] = [];
  peak = 0;
  override = false;
  seeding: UpdateDownloadJob[] = [];
  private last = Date.now();
  private carry = 0;
  private demoLoaded = false;

  constructor(
    private readonly gameRunning: () => boolean,
    private readonly settings: () => DownloadQueueSettings,
  ) {}

  private demo(): void {
    if (this.demoLoaded) return;
    this.demoLoaded = true;
    if (localStorage.getItem("wlib-mock-download-demo") !== "1") return;
    const ids = readMockGames().map((game) => game.id);
    const pick = (index: number): number => ids[index % Math.max(ids.length, 1)] ?? index + 1;
    const add = (index: number, kind: "host" | "torrent", host: string, size: number | null, patch: Partial<MockJob>): void => {
      const job = makeJob(kind, pick(index), `0.${index + 1}`, kind === "torrent" ? { rpdl_id: 100 + index } : { label: "Win/Linux", platform: "windows/linux", host, part: "" }, size);
      Object.assign(job, patch);
      this.jobs.push(job);
    };
    add(0, "host", "pixeldrain", 1.2 * 1024 * MIB, { state: "attention", error_code: "network", error_text: "network error", bytes_done: 520 * MIB, retries: 3 });
    add(1, "torrent", "", 2.4 * 1024 * MIB, { state: "running", bytes_done: 800 * MIB, details: { stage: "downloading", peers: 14 } });
    add(2, "host", "mega", 900 * MIB, { state: "running", can_continue: false, bytes_done: 300 * MIB, details: { stage: "downloading", host: "mega" } });
    add(3, "host", "buzzheavier", null, { state: "queued", details: { stage: "waiting", host: "buzzheavier" } });
    add(4, "host", "pixeldrain", 640 * MIB, { state: "retry_wait", error_code: "network", error_text: "network error", retries: 1, retry_at: now() + 30, bytes_done: 120 * MIB });
    add(5, "torrent", "", 3 * 1024 * MIB, { state: "paused", bytes_done: 1.1 * 1024 * MIB, details: { stage: "paused" } });
    add(6, "host", "pixeldrain", 450 * MIB, { state: "ready", bytes_done: 450 * MIB, finished_at: now() - 3600, details: { stage: "done", host: "pixeldrain", archive_path: "/home/user/.local/share/wLib/downloads/Demo-0.7.zip" } });
    this.seeding = [{
      job_id: "mock-seed", game_id: pick(7), kind: "torrent", stage: "done", title: titleOf(pick(7)), version: "1.0",
      bytes: 2 * 1024 * MIB, total: 2 * 1024 * MIB, speed: 0, error: "", archive_path: "", seed_state: "seeding",
      ratio: 0.4, ratio_target: 1, upload_speed: 240 * 1024, peers: 3,
    }];
    this.renumber();
  }

  private renumber(): void {
    this.jobs.forEach((job, index) => { job.position = index; });
  }

  private hold(): "" | "game" | "battery" {
    return this.settings().download_pause_while_playing && this.gameRunning() ? "game" : "";
  }

  private holdActive(): boolean {
    return Boolean(this.hold()) && !this.override;
  }

  private running(): MockJob[] {
    return this.jobs.filter((job) => job.state === "running");
  }

  private start(job: MockJob): void {
    job.state = "running";
    job.reason = "";
    job.startedAt = Date.now();
    job.error_code = "";
    job.error_text = "";
    job.details = { ...job.details, stage: "downloading", ...(job.kind === "torrent" ? { peers: 6 } : {}) };
  }

  private fill(): void {
    if (this.holdActive()) return;
    let running = this.running().length;
    for (const job of this.jobs) {
      if (running >= this.settings().download_max_active) break;
      if (job.state !== "queued") continue;
      this.start(job);
      running += 1;
    }
  }

  private advance(): void {
    const time = Date.now();
    const elapsed = Math.min(Math.max((time - this.last) / 1000, 0), 600);
    this.last = time;
    if (!this.hold()) this.override = false;
    if (this.holdActive()) {
      for (const job of this.running()) {
        if (job.can_continue) { job.state = "held"; job.reason = this.hold(); }
      }
    } else {
      const held = this.jobs.filter((job) => job.state === "held");
      held.forEach((job) => { job.state = "queued"; job.reason = ""; });
      this.jobs = [...held, ...this.jobs.filter((job) => !held.includes(job))];
      this.renumber();
    }
    for (const job of this.jobs) {
      if (job.state === "preparing" && job.readyAt !== undefined && time >= job.readyAt) {
        job.state = "queued";
        job.details = { ...job.details, stage: "waiting" };
        if (job.bytes_total === null && job.source.host !== "buzzheavier") job.bytes_total = (300 + (job.game_id % 7) * 180) * MIB;
      }
      if (job.state === "retry_wait" && job.retry_at !== null && now() >= job.retry_at) {
        job.state = "queued";
        job.retry_at = null;
      }
    }
    this.fill();
    const running = this.running();
    const limit = this.settings().download_limit_kib * 1024;
    let total = 0;
    for (const job of running) {
      const base = job.kind === "torrent" ? TORRENT_SPEED : HOST_SPEED;
      const speed = limit > 0 ? Math.min(base, limit / running.length) : base;
      job.speed = speed;
      total += speed;
      job.bytes_done += speed * elapsed;
      if (job.bytes_total !== null && job.bytes_done >= job.bytes_total) {
        job.bytes_done = job.bytes_total;
        job.state = "ready";
        job.speed = 0;
        job.finished_at = now();
        job.details = { ...job.details, stage: "done", archive_path: `/home/user/.local/share/wLib/downloads/${job.title.replace(/\W+/g, "-")}-${job.version}.zip` };
        if (job.kind === "torrent") job.details.seed_state = "awaiting_answer";
      }
      job.eta = job.bytes_total !== null && speed > 0 ? Math.round((job.bytes_total - job.bytes_done) / speed) : null;
    }
    for (const job of this.jobs) if (job.state !== "running") { job.speed = 0; job.eta = null; }
    this.fill();
    this.carry += elapsed;
    while (this.carry >= 1) {
      this.carry -= 1;
      this.history.push(total);
      if (this.history.length > HISTORY) this.history.shift();
    }
    this.peak = Math.max(this.peak, total);
  }

  status(includeHistory: boolean): DownloadQueueStatus {
    this.demo();
    this.advance();
    const unfinished = this.jobs.filter((job) => job.state !== "ready");
    const needed = unfinished.reduce((sum, job) => sum + (job.bytes_total ? Math.max(0, job.bytes_total - job.bytes_done) : 0), 0);
    const free = 212 * 1024 ** 3;
    const hold = this.holdActive() ? this.hold() : "";
    const speed = this.running().reduce((sum, job) => sum + job.speed, 0);
    return {
      success: true,
      jobs: this.jobs.map((job) => ({
        ...job,
        bytes_done: Math.round(job.bytes_done),
        retry_in: job.state === "retry_wait" && job.retry_at !== null ? Math.max(0, Math.round(job.retry_at - now())) : null,
        details: { ...job.details },
        source: { ...job.source },
      })),
      queue: {
        max_active: this.settings().download_max_active,
        active: this.running().length,
        hold,
        hold_reason: this.hold(),
        hold_override: this.override,
        hold_keeps_running: hold ? this.running().filter((job) => !job.can_continue).map((job) => job.job_id) : [],
        speed,
        peak: Math.max(this.peak, speed),
        ...(includeHistory ? { history: [...this.history] } : {}),
        forecast: {
          needed, free, fits: needed <= free, missing: Math.max(0, needed - free),
          unknown: unfinished.filter((job) => !job.bytes_total).length,
        },
        settings: this.settings(),
      },
      seeding: this.seeding.map((job) => ({ ...job })),
    };
  }

  add(kind: "host" | "torrent", gameId: number, version: string, source: DownloadJobSource, replaces: string | null): { success: boolean; error?: string; job_id?: string } {
    this.demo();
    const replaced = replaces ? this.jobs.find((job) => job.job_id === replaces) : undefined;
    if (replaces && !replaced) return { success: false, error: "The download to replace was not found" };
    if (this.jobs.some((job) => job.game_id === gameId && job.state !== "ready" && job !== replaced)) {
      return { success: false, error: "This game is already in the downloads" };
    }
    const job = makeJob(kind, gameId, version, source, kind === "torrent" ? 1.6 * 1024 * MIB : null);
    if (replaced) this.jobs[this.jobs.indexOf(replaced)] = job;
    else this.jobs.push(job);
    this.renumber();
    return { success: true, job_id: job.job_id };
  }

  private find(jobId: unknown): MockJob | undefined {
    return typeof jobId === "string" ? this.jobs.find((job) => job.job_id === jobId) : undefined;
  }

  command(name: string, args: unknown[]): Record<string, unknown> {
    this.demo();
    this.advance();
    const notFound = { success: false, error: "The download was not found" };
    switch (name) {
      case "reorder_downloads": {
        const ids = Array.isArray(args[0]) ? (args[0] as unknown[]).filter((id): id is string => typeof id === "string") : null;
        if (!ids) return { success: false, error: "The order must be a list of downloads" };
        const named = ids.map((id) => this.find(id)).filter((job): job is MockJob => Boolean(job));
        this.jobs = [...named, ...this.jobs.filter((job) => !named.includes(job))];
        this.renumber();
        this.fill();
        return { success: true };
      }
      case "start_download_now": {
        const job = this.find(args[0]);
        if (!job) return notFound;
        if (!["queued", "retry_wait", "held", "paused"].includes(job.state)) return { success: false, error: "The download cannot start now" };
        this.jobs = [job, ...this.jobs.filter((item) => item !== job)];
        job.state = "queued";
        job.retry_at = null;
        if (this.holdActive()) { this.renumber(); return { success: true, outcome: "first" }; }
        const running = this.running().sort((a, b) => b.startedAt - a.startedAt);
        if (running.length < this.settings().download_max_active) {
          this.start(job);
          this.renumber();
          return { success: true, outcome: "started" };
        }
        const victim = running.find((item) => item.can_continue);
        if (!victim) { this.renumber(); return { success: true, outcome: "first" }; }
        victim.state = "queued";
        this.start(job);
        this.jobs = [job, victim, ...this.jobs.filter((item) => item !== job && item !== victim)];
        this.renumber();
        return { success: true, outcome: "yielded" };
      }
      case "pause_download": {
        const job = this.find(args[0]);
        if (!job) return notFound;
        if (!["running", "preparing", "queued", "retry_wait", "held"].includes(job.state)) return { success: false, error: "This download cannot be paused" };
        if (!job.can_continue && job.state === "running") job.bytes_done = 0;
        job.state = "paused";
        job.retry_at = null;
        this.fill();
        return { success: true };
      }
      case "resume_download": {
        const job = this.find(args[0]);
        if (!job) return notFound;
        if (job.state !== "paused") return { success: false, error: "This download is not paused" };
        job.state = "queued";
        this.fill();
        return { success: true };
      }
      case "pause_all_downloads": {
        const paused = this.jobs.filter((job) => ["running", "preparing", "queued", "retry_wait", "held"].includes(job.state));
        paused.forEach((job) => { job.state = "paused"; job.retry_at = null; });
        return { success: true, count: paused.length };
      }
      case "resume_all_downloads": {
        const paused = this.jobs.filter((job) => job.state === "paused");
        paused.forEach((job) => { job.state = "queued"; });
        this.fill();
        return { success: true, count: paused.length };
      }
      case "retry_download": {
        const job = this.find(args[0]);
        if (!job) return notFound;
        if (job.state !== "attention" && job.state !== "retry_wait") return { success: false, error: "This download has not failed" };
        job.state = "queued";
        job.retries = 0;
        job.retry_at = null;
        job.error_code = "";
        job.error_text = "";
        this.fill();
        return { success: true };
      }
      case "remove_download": {
        const job = this.find(args[0]);
        if (!job) return notFound;
        this.jobs = this.jobs.filter((item) => item !== job);
        this.renumber();
        this.fill();
        return { success: true };
      }
      case "resume_downloads_now":
        if (!this.hold()) return { success: false, error: "Downloads are not waiting" };
        this.override = true;
        this.fill();
        return { success: true };
      case "stop_seeding": {
        const before = this.seeding.length;
        this.seeding = this.seeding.filter((job) => job.job_id !== args[0]);
        return before === this.seeding.length ? { success: false, error: "The torrent is not seeding" } : { success: true };
      }
      case "answer_seed_prompt": {
        const job = this.jobs.find((item) => item.job_id === args[0] && item.details.seed_state === "awaiting_answer");
        if (!job || typeof args[1] !== "boolean") return { success: false, error: "That question was already answered" };
        job.details = { ...job.details, seed_state: args[1] ? "seeding" : "ended" };
        if (args[1]) {
          this.seeding.push({
            job_id: job.job_id, game_id: job.game_id, kind: "torrent", stage: "done", title: job.title, version: job.version,
            bytes: job.bytes_total ?? 0, total: job.bytes_total, speed: 0, error: "", archive_path: job.details.archive_path ?? "",
            seed_state: "seeding", ratio: 0, ratio_target: 1, upload_speed: 120 * 1024, peers: 2,
          });
        }
        return { success: true };
      }
      default:
        return { success: false, error: `Unknown download command ${name}` };
    }
  }

  /** A failed download, for trying the "Needs you" actions in the preview. */
  fail(jobId: string, code: DownloadQueueJob["error_code"], text: string): void {
    const job = this.find(jobId);
    if (!job) return;
    if (code === "network" && job.retries < RETRY_DELAYS.length) {
      job.state = "retry_wait";
      job.retry_at = now() + (RETRY_DELAYS[job.retries] ?? 30);
      job.retries += 1;
    } else {
      job.state = "attention";
    }
    job.error_code = code;
    job.error_text = text;
  }
}
