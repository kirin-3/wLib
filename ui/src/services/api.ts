// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * A wrapper service to communicate with the Python backend via PyWebView.
 * It gracefully falls back to mock data if the webview object isn't injected
 * (e.g., when running in normal browser).
 */

import type { LaunchMode } from "../utils/launchMode";
import type { LibraryFilterPreset } from "../utils/libraryViewState";
import { hasAvailableUpdate } from "../utils/libraryGames";
import type { MockBackend } from "./mock/backend";
export type { LaunchMode } from "../utils/launchMode";
export { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../utils/platformPolicy";

type ApiInvoker = (...args: unknown[]) => Promise<unknown> | unknown;

interface PyWebViewBridge {
  api?: Record<string, ApiInvoker>;
}

export interface GameScreenshotsResponse extends ApiBasicResponse {
  screenshots: { thumb: string; full: string }[];
  source: "catalog" | "library";
  in_catalog: boolean;
}

export interface ApiErrorResult {
  success: false;
  error: string;
  error_code?: string;
  mock?: boolean;
}

export interface ApiBasicResponse {
  success?: boolean;
  error?: string;
  error_code?: string;
  mock?: boolean;
  code?: string;
}

export interface DownloadAccounts extends ApiBasicResponse {
  pixeldrain: { connected: boolean; username: string };
  mega: { connected: boolean; email: string };
  rpdl: { connected: boolean; username: string };
}
export interface RpdlConnectResponse extends ApiBasicResponse { username?: string }
/** One RPDL search result, scored against the game. */
export interface RpdlTorrent {
  id: number;
  title: string;
  size: number;
  seeders: number;
  leechers: number;
  /** Unix time of the upload. */
  date: number;
  /** 0-100 match against the game's title. */
  score: number;
  /** Whether the title carries the requested version; null when none was requested. */
  version_match: boolean | null;
  no_seeders: boolean;
}
export interface RpdlSearchResponse extends ApiBasicResponse {
  results?: RpdlTorrent[];
  /** The one torrent that clearly wins, or null when the user must choose. */
  preselected_id?: number | null;
  needs_choice?: boolean;
  signed_in?: boolean;
  query?: string;
  error_code?: string;
}
/** Result of Save .torrent or Send to client. `path` is the written `.torrent` file. */
export interface RpdlTorrentExportResponse extends ApiBasicResponse {
  path?: string;
  /** The watch folder, only for Send to client. */
  folder?: string;
  error_code?: string;
}
/** The magnet holds the user's RPDL passkey: use it only for the clipboard write, never store or log it. */
export interface RpdlMagnetResponse extends ApiBasicResponse {
  magnet?: string;
  error_code?: string;
}
export interface PixeldrainConnectResponse extends ApiBasicResponse { username?: string }
export interface MegaConnectResponse extends ApiBasicResponse { email?: string; needs_code?: boolean }

export type SteamMode = "off" | "all-installed" | "marked";
export interface SteamStatus extends ApiBasicResponse {
  found: boolean;
  flatpak_steam: boolean;
  accounts: string[];
  account_id: string;
  running: boolean;
  deck: boolean;
  gaming_mode: boolean;
  mode: SteamMode;
  sync: ApiBasicResponse & { running: boolean; added?: number; updated?: number; removed?: number; pending?: number };
}
export interface SteamCoverConversion {
  token: string;
  type: string;
  data: string;
}
export interface SteamGameState extends ApiBasicResponse {
  eligible?: boolean;
  reason?: "" | "wishlisted" | "missing_file" | "browser_game";
  state?: string;
  marked?: boolean;
  mode?: SteamMode;
}

export interface ApiSuccessResponse {
  success: true;
}

export interface AddGameResponse extends ApiBasicResponse {
  id?: number | null;
  title?: string;
  metadata_pending?: boolean;
}

// finished_version is present only when the backend auto-filled it by
// moving the game to Completed.
export interface UpdateGameResponse extends ApiBasicResponse {
  finished_version?: string;
  skipped_version?: string;
}

// Title, version and engine guessed from a game's install folder.
export interface GameGuess {
  title: string;
  version: string;
  engine: string;
  exe_path: string;
}

export interface InspectGamePathResponse extends ApiBasicResponse, Partial<GameGuess> {}

export interface ScannedGame extends GameGuess {
  in_library: boolean;
}

export interface ScanGamesFolderResponse extends ApiBasicResponse {
  games?: ScannedGame[];
}

export interface ExtensionSyncStatus extends ApiBasicResponse {
  success: boolean;
  updated?: boolean;
  installed_version?: string;
  bundled_version?: string;
  path?: string;
  reason?: string;
}

export interface ExtensionBridgeStatus {
  connected: boolean;
  connections: number;
  eligible: boolean;
  extension_version: string;
  can_fetch: boolean | null;
  connected_seconds: number | null;
  disconnects: number;
  last_connection_seconds: number | null;
  last_disconnect_seconds_ago: number | null;
  last_fetch: {
    seconds_ago: number;
    status: number | null;
    signed_in: boolean | null;
    error: string;
  } | null;
}

export interface ExtensionPairingEntry {
  /** Short code, also shown on the extension's options page. */
  id: string;
  browser: string;
  origin: string;
  at: string;
}

export interface ExtensionPairingResponse extends ApiBasicResponse {
  pending: ExtensionPairingEntry[];
  paired: ExtensionPairingEntry[];
}

export interface ExtensionServiceStatus extends ApiBasicResponse {
  success: boolean;
  reachable: boolean;
  bridge?: ExtensionBridgeStatus;
}

export interface RunnerInfo {
  name: string;
  path: string;
}

export interface UmuStatus extends ApiBasicResponse {
  source?: string;
  path?: string;
  version?: string;
  python3?: boolean;
}

export interface UmuJobStatus extends ApiBasicResponse {
  running?: boolean;
  stage?: string;
  done?: number;
  total?: number;
  error_class?: string;
  cancelled?: boolean;
  runtime?: string;
  path?: string;
  ready?: boolean;
}

export interface GetAvailableRunnersResponse extends ApiBasicResponse {
  success: boolean;
  runners: RunnerInfo[];
}

export interface LaunchGameResponse extends ApiBasicResponse {
  success: boolean;
  notice?: string;
  /** Set with code "web_media_unsupported". */
  media?: WebMediaInfo;
}

export interface FfmpegInstallHint {
  command: string;
  package_manager: string;
  platform: "linux" | "windows";
  /** True in the Flatpak: install ffmpeg on the host system. */
  host: boolean;
}

export interface FfmpegInfo {
  available: boolean;
  ffmpeg: string;
  ffprobe: string;
  video_encoder: string;
  audio_encoder: string;
  /** "", "missing", "no_video_encoder" or "no_audio_encoder". */
  reason: string;
  install: FfmpegInstallHint;
}

/** Videos the built-in player cannot decode (H.264, H.265, AAC). */
export interface WebMediaInfo {
  count: number;
  ffmpeg: FfmpegInfo;
}

export interface WebMediaStatus extends ApiBasicResponse {
  running: boolean;
  finished: boolean;
  done: number;
  total: number;
  current: string;
  converted: number;
  failed: number;
  failed_files: string[];
  cancelled: boolean;
  error: string;
}

export interface UpdateCheckResponse extends ApiBasicResponse {
  skipped?: boolean;
  success: boolean;
  version?: string;
  has_update?: boolean;
  newly_finished?: boolean;
  metadata_updated?: number;
  browser_fallback?: "unavailable" | "failed" | "logged_out";
}

export interface BulkUpdateStartResponse extends ApiBasicResponse {
  success: boolean;
  total?: number;
  delay_seconds?: number;
}

export interface BulkUpdateResultItem {
  id: number;
  title: string;
  current_version: string;
  latest_version: string;
  has_update: boolean;
  skipped?: boolean;
  newly_finished?: boolean;
  error: string;
  error_code: string;
  browser_fallback?: "unavailable" | "failed" | "logged_out";
  // Present on "Refresh F95 info" runs only.
  info_changed?: boolean;
  previous?: RefreshInfoPrevious;
}

// Old values of only the fields that a "Refresh F95 info" run changed.
export interface RefreshInfoPrevious {
  cover_image_path?: string;
  rating?: string;
  rating_updated_at?: string;
  tags?: string;
  f95_tags?: string | null;
}

export interface UpdateStatusResponse extends ApiBasicResponse {
  running: boolean;
  outcome?: "idle" | "running" | "completed" | "cancelled" | "failed";
  cancelling?: boolean;
  total: number;
  checked: number;
  current: string;
  results: BulkUpdateResultItem[];
  delay_seconds: number;
  refresh_info?: boolean;
}

export interface AutoCheckSettingResponse extends ApiBasicResponse {
  frequency: string;
  last_check: string;
}

export interface MaybeAutoCheckResponse {
  triggered: boolean;
  result?: BulkUpdateStartResponse;
  reason?: string;
}

// done/total are bytes while downloading; total is 0 when the size is unknown.
export interface ProtonDownloadStatus {
  running: boolean;
  stage: string;
  done: number;
  total: number;
}

export interface InstallProgressStatus {
  running: boolean;
  done: number;
  total: number;
  current: string;
  error: string;
}

export interface InstallStatusResponse extends ApiBasicResponse {
  deps: InstallProgressStatus;
  rtps: InstallProgressStatus;
  dlls_installed: boolean;
  rtps_installed: boolean;
}

export interface SystemDepsCommandResponse {
  detected: boolean;
  package_manager: string;
  distro: string;
  command: string;
  // Hosts without Linux runtime installers report why, with an error code.
  error?: string;
  code?: string;
}

export interface DownloadProtonResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
}

export interface AppReleaseAsset {
  name: string;
  url: string;
}

export interface AppUpdateResponse extends ApiBasicResponse {
  success: boolean;
  version?: string;
  current_version?: string;
  changelog?: string;
  url?: string;
  assets?: AppReleaseAsset[];
}

export interface AppVersionResponse {
  version: string;
}

// `supported` is false on hosts that cannot inject into a Wine prefix.
export interface CheatEngineStatusResponse {
  installed: boolean;
  path: string;
  supported?: boolean;
}

export interface DownloadCheatEngineResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
}

export interface ExecutableModifiedTimeResponse extends ApiBasicResponse {
  success: boolean;
  modified_at: string | null;
}

export interface ThreadDetailsResponse extends ApiBasicResponse {
  success: boolean;
  title?: string;
  version?: string;
  creator?: string;
  engine?: string;
  cover_image?: string;
  tags?: string[] | string;
}

export interface LibraryFolder {
  path: string;
  is_default: boolean;
  available: boolean;
  free_bytes: number | null;
}

/** One game in an uninstall or move plan (`plan_game_uninstall`, `plan_game_move`). */
export interface GameFilePlanEntry {
  game_id: number;
  title: string;
  /** The folder that is deleted or moved. */
  root: string;
  root_source: "pattern" | "exe_dir" | "override" | "";
  /** The unsafe-folder guard result for `root`. */
  removable: boolean;
  reason: string;
  /** The saves folder when it exists, or "". */
  saves_folder: string;
  saves_inside_root: boolean;
  /** Running, busy, not installed or not found. */
  blocked_reason: string;
  /** Move only: the final folder. */
  target?: string;
  same_device?: boolean;
  /** Move only: target exists, destination inside the root, not enough space and so on. */
  move_reason?: string;
  size_bytes?: number | null;
}
export interface GameFilePlanResponse extends ApiBasicResponse {
  games?: GameFilePlanEntry[];
}
export interface UninstallOptions {
  backup_saves: boolean;
  remove_from_library: boolean;
  /** Folder chosen by the user for a game, keyed by game id. The backend checks it again. */
  root_overrides?: Record<string, string>;
}
export interface UninstallResult {
  game_id: number;
  success: boolean;
  error: string;
  trash?: "library" | "recorded" | "in_place";
}
export interface UninstallGamesResponse extends ApiBasicResponse {
  results?: UninstallResult[];
  uninstalled?: number;
  failed?: number;
}
export interface MoveSummary {
  moved: number;
  skipped: number;
  cancelled: number;
  failed: number;
  first_reason: string;
}
export interface MoveCurrent {
  game_id: number;
  title: string;
  bytes_done: number;
  /** 0 for a rename on the same drive. */
  bytes_total: number;
  speed_bps: number;
}
export interface MoveStatus extends ApiBasicResponse {
  running: boolean;
  current: MoveCurrent | null;
  waiting: number[];
  /** "Moving position of total". */
  position: number;
  total: number;
  /** Shows once, after the queue ends. */
  summary?: MoveSummary;
}
export interface StartMoveResponse extends ApiBasicResponse {
  queued?: number[];
}

export interface InstallDestinationOptions {
  library_folder?: string | undefined;
  title: string;
  version: string;
  creator: string;
  engine: string;
  game_id?: number | undefined;
}

export interface InstallDestinationResponse {
  destination: string;
  engine_pending: boolean;
}

export interface ArchiveInspectResponse extends ApiBasicResponse {
  success: boolean;
  title?: string;
  version?: string;
  creator?: string;
  destination?: string;
  engine?: string;
  engine_pending?: boolean;
  tool_error?: string;
  old_folder?: string;
  removable?: boolean;
  remove_reason?: string;
  library_folder?: string;
  own_targets_inside?: string[];
  /** The game being updated lies outside every listed library folder. */
  outside_library_folders?: boolean;
  /** Folder to offer as a new library folder ("" when none is safe to suggest). */
  suggested_library_folder?: string;
  downloaded_by_wlib?: boolean;
  /** "folder" when the source is a game folder downloaded by a torrent. */
  source_kind?: "archive" | "folder";
  /** The source lies in wLib's torrent staging area. */
  torrent_source?: boolean;
  /** Its torrent is still sharing the files, so install copies them and keeps them. */
  torrent_seeding?: boolean;
}

export interface UpdateMirror { link_id: string; host: string; label: string; part: string; split: boolean; /** False when wLib cannot download it itself (a direct Buzzheavier link). */ in_app?: boolean }
export interface UpdateBuild { label: string; section: string; platform: string; kind: string; mirrors: UpdateMirror[] }
export interface UpdateDownloadLinks extends ApiBasicResponse { version?: string; builds?: UpdateBuild[] }
export interface UpdateDownloadJob {
  job_id: string;
  game_id: number;
  stage: string;
  bytes: number;
  total: number | null;
  speed: number;
  error: string;
  archive_path: string;
  /** Present on torrent jobs. */
  kind?: "torrent";
  title?: string;
  version?: string;
  /** Torrent folder to install from when the torrent holds no single archive. */
  folder_path?: string;
  payload_kind?: "archive" | "folder" | "";
  /** An install from this torrent already finished. */
  installed?: boolean;
  peers?: number;
  upload_speed?: number;
  ratio?: number;
  ratio_target?: number;
  /** "", "awaiting_answer", "seeding" or "ended". */
  seed_state?: string;
  paused?: boolean;
  /** What a running torrent is waiting for, such as "Looking for peers". */
  hint?: string;
  /** Seconds left, when known. */
  eta?: number | null;
}

/** Queue states (design D3). Only "running" uses a slot. */
export type DownloadQueueState = "preparing" | "queued" | "retry_wait" | "held" | "running" | "paused" | "attention" | "ready";
/** Failure codes from core/downloads/failures.py; the UI owns the words. */
export type DownloadFailureCode =
  | "network" | "removed" | "quota" | "challenge" | "invalid" | "disk" | "write" | "sign_in"
  | "extension" | "check_timeout" | "permission" | "mirror_gone" | "engine" | "unknown";
export interface DownloadJobDetails {
  /** Worker step: resolving, needs_user, downloading, verifying, fetching, paused, done, ... */
  stage?: string;
  host?: string;
  hint?: string;
  peers?: number;
  upload_speed?: number;
  ratio?: number;
  ratio_target?: number;
  /** "", "awaiting_answer", "seeding" or "ended". */
  seed_state?: string;
  archive_path?: string;
  folder_path?: string;
  payload_kind?: "archive" | "folder" | "";
  installed?: boolean;
  restarting?: boolean;
}
export interface DownloadJobSource { label?: string; section?: string; platform?: string; host?: string; part?: string; rpdl_id?: number }
export interface DownloadQueueJob {
  job_id: string;
  kind: "host" | "torrent";
  game_id: number;
  title: string;
  version: string;
  state: DownloadQueueState;
  /** "game" or "battery" for held; "browser_check" for a check in the browser; "preparing" for a pause while preparing. */
  reason: string;
  position: number;
  error_code: DownloadFailureCode | "";
  error_text: string;
  bytes_done: number;
  bytes_total: number | null;
  /** Bytes per second while running. */
  speed: number;
  eta: number | null;
  retries: number;
  /** Unix time of the next automatic retry. */
  retry_at: number | null;
  retry_in: number | null;
  created_at: number;
  finished_at: number | null;
  /** False when a pause or hold would start the download over (MEGA). */
  can_continue: boolean;
  source: DownloadJobSource;
  details: DownloadJobDetails;
}
export interface DownloadQueueSettings {
  /** KiB/s for file hosts and torrents together; 0 means unlimited. */
  download_limit_kib: number;
  /** 1 to 5. */
  download_max_active: number;
  download_pause_while_playing: boolean;
  download_pause_on_battery: boolean;
}
export interface DownloadForecast { needed: number; free: number | null; unknown: number; fits: boolean; missing: number }
export interface DownloadQueueSummary {
  max_active: number;
  active: number;
  /** The hold in force: "game", "battery", or "" (none, or overridden with Resume now). */
  hold: "" | "game" | "battery";
  hold_reason: "" | "game" | "battery";
  hold_override: boolean;
  /** Running downloads that cannot continue later, so a hold leaves them running. */
  hold_keeps_running: string[];
  speed: number;
  peak: number;
  /** One combined-speed sample per second, the last 5 minutes; only when asked for. */
  history?: number[];
  forecast: DownloadForecast;
  settings: DownloadQueueSettings;
}
/** "started": it runs now; "yielded": a running download gave up its slot; "first": it waits at the top. */
export interface StartDownloadNowResponse extends ApiBasicResponse { outcome?: "started" | "yielded" | "first" }
export interface DownloadQueueStatus extends ApiBasicResponse {
  jobs?: DownloadQueueJob[];
  queue?: DownloadQueueSummary;
  /** Torrent snapshots of finished torrents that still seed. */
  seeding?: UpdateDownloadJob[];
}

export interface ArchiveInstallOptions {
  target_id?: number | null | undefined;
  links?: StoreLink[] | undefined;
  archive_path: string;
  game_id?: number | null | undefined;
  title?: string | undefined;
  version?: string | undefined;
  creator?: string | undefined;
  engine?: string | undefined;
  f95_url?: string | undefined;
  cover_image?: string | undefined;
  tags?: string | string[] | undefined;
  rating?: string | undefined;
  library_folder?: string | undefined;
  pattern?: string | undefined;
  remove_old?: boolean | undefined;
  delete_archive?: boolean | undefined;
  /** "folder" installs a game folder from a finished torrent; archive_path then names that folder. */
  source_kind?: "archive" | "folder" | undefined;
  /** Launch file inside the archive, chosen by the user when an update found none. */
  exe_relpath?: string | undefined;
}

export interface ArchiveInstallResponse extends ApiBasicResponse {
  success: boolean;
  /** Launch files in the archive (paths inside it) when an update could not pick one. */
  launch_candidates?: string[];
  game_id?: number;
  exe_path?: string;
  destination?: string;
  unmoved_targets?: Array<Record<string, unknown>>;
  kept_old_folder_reason?: string;
  removed_old?: boolean;
  old_folder?: string;
  needs_executable?: boolean;
}

export interface ArchiveInstallStatus {
  running: boolean;
  stage: string;
  /** Extracted bytes so far and the archive's unpacked size (0 when unknown). */
  done: number;
  total: number;
  /** True until the new version starts moving into place. */
  cancellable?: boolean;
}

export interface DownloadOffer {
  game_id: number;
  game_title: string;
  installed_version: string;
  latest_version: string;
  archive_path: string;
  archive_version: string;
  guessed_title: string;
  key: string;
}

export interface DownloadOffersResponse extends ApiBasicResponse {
  success: boolean;
  offers?: DownloadOffer[];
}

export interface LaunchTarget {
  id: number;
  game_id: number;
  label: string;
  exe_path: string;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface LaunchTargetResponse extends ApiBasicResponse {
  success: boolean;
  target?: LaunchTarget;
}

export interface LaunchTargetsResponse extends ApiBasicResponse {
  success: boolean;
  targets: LaunchTarget[];
}

export interface GameAttachment {
  name: string;
  size: number;
}

export interface GameAttachmentsResponse extends ApiBasicResponse {
  success: boolean;
  attachments: GameAttachment[];
}

export interface AddGameAttachmentResponse extends ApiBasicResponse {
  success: boolean;
  attachment?: GameAttachment;
  attachments?: GameAttachment[];
}

export interface RemoveGameAttachmentResponse extends ApiBasicResponse {
  success: boolean;
  attachments?: GameAttachment[];
}

export type LibraryBackupSection =
  | "metadata"
  | "covers"
  | "user_state"
  | "launch_config"
  | "executable_paths"
  | "launch_targets"
  | "settings_general"
  | "settings_paths";

export interface LibraryBackupOptions {
  sections: LibraryBackupSection[];
}

export interface LibraryBackupWarning {
  type: string;
  scope: string;
  message: string;
  field?: string;
  title?: string;
  path?: string;
}

export interface LibraryBackupCounts {
  companion_tools?: number;
  total_games: number;
  matched_games: number;
  new_games: number;
  ambiguous_games: number;
  warnings: number;
}

export interface LibraryBackupManifest {
  format?: string;
  format_version?: number;
  exported_at?: string;
  app?: Record<string, unknown>;
  selected_sections?: LibraryBackupSection[];
}

export interface LibraryBackupAmbiguousGame {
  title: string;
  reason: string;
  candidate_count: number;
}

export interface LibraryBackupExportResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
  selected_sections?: LibraryBackupSection[];
  game_count?: number;
  encrypted?: boolean;
}

export type LibrarySyncState = "off" | "idle" | "syncing" | "needs_choice" | "needs_passphrase" | "error" | "offline" | "paused";
export type LibrarySyncChoice = "use-remote" | "use-local" | "merge-once";
export interface SyncSnapshot {
  name: string;
  created_at: string;
  games: number;
  bytes: number;
}

export interface SyncSnapshotList extends ApiBasicResponse {
  snapshots?: SyncSnapshot[];
}

export interface SyncSnapshotRestore extends ApiBasicResponse {
  created?: number;
  updated?: number;
  deleted?: number;
}

export interface LibrarySyncStatus extends ApiBasicResponse {
  state?: LibrarySyncState;
  /** True while this device is paused, also while a run started before the pause finishes. */
  paused?: boolean;
  location?: "" | "folder" | "dropbox" | "gdrive";
  folder_path?: string;
  account?: string;
  encrypted?: boolean;
  last_synced_at?: string;
  last_device?: string;
  last_error?: string;
  dropbox_available?: boolean;
  dropbox_unavailable_reason?: string;
  gdrive_available?: boolean;
  gdrive_unavailable_reason?: string;
  connecting?: boolean;
  /** Why a library choice is needed; set while state is "needs_choice". */
  choice_reason?: "" | "first" | "conflict" | "removal";
}

export interface LibraryBackupInspectResponse extends ApiBasicResponse {
  success: boolean;
  manifest?: LibraryBackupManifest;
  available_sections?: LibraryBackupSection[];
  counts?: LibraryBackupCounts;
  warnings?: LibraryBackupWarning[];
  ambiguous_games?: LibraryBackupAmbiguousGame[];
}

export interface LibraryBackupImportResponse extends ApiBasicResponse {
  success: boolean;
  created?: number;
  updated?: number;
  deleted?: number;
  skipped?: number;
  ambiguous?: number;
  warnings?: number;
  warning_records?: LibraryBackupWarning[];
  settings_updated?: number;
  companion_tools_updated?: number;
  companion_tools_skipped?: number;
  selected_sections?: LibraryBackupSection[];
  created_ids?: number[];
  updated_ids?: number[];
}

export type ExternalLibrarySource = "f95checker" | "atlas" | "xlibrary" | "f69" | "playnite";
export type ExternalLibraryMergeMode = "overwrite" | "fill_missing";

export interface ExternalLibraryDetectResponse extends ApiBasicResponse {
  success: boolean;
  source?: string;
  path?: string;
  checked?: string[];
}

export interface ExternalLibraryCounts {
  covers_to_copy?: number;
  covers_to_fill?: number;
  total_games: number;
  matched_games: number;
  new_games: number;
  ambiguous_games: number;
  skipped_no_identity: number;
  dropped_paths: number;
  warnings: number;
}

export interface ExternalLibraryInspectResponse extends ApiBasicResponse {
  success: boolean;
  source?: string;
  schema_version?: string | number;
  available_sections?: LibraryBackupSection[];
  counts?: ExternalLibraryCounts;
  warnings?: LibraryBackupWarning[];
  ambiguous_games?: LibraryBackupAmbiguousGame[];
}

export interface ExternalLibraryImportOptions {
  sections?: LibraryBackupSection[];
  merge_mode?: ExternalLibraryMergeMode;
}

// The external reader always applies metadata to matches and reports the
// follow-up metadata check the backend started for the imported games.
export interface ExternalLibraryImportResponse extends LibraryBackupImportResponse {
  source?: string;
  schema_version?: string | number;
  skipped_no_identity?: number;
  dropped_paths?: number;
  metadata_check?: "started" | "busy" | "none";
}

export interface SaveLocation {
  path: string;
  type: string;
  description: string;
  // false for the game's own folder: a lead only, never an automatic target
  eligible: boolean;
}

export type SaveBackupKeep = number | "all";

export interface GameSaveBackup {
  name: string;
}

export interface GameSavesResponse extends ApiBasicResponse {
  override: string;
  override_exists: boolean;
  detected: SaveLocation[];
  effective: string;
  effective_exists: boolean;
  backups: GameSaveBackup[];
  keep: SaveBackupKeep;
}

export interface GameSaveFolderResponse extends ApiBasicResponse {
  override?: string;
  override_exists?: boolean;
}

export interface GameSaveActionResponse extends ApiBasicResponse {
  message?: string;
  backup?: string;
  backups?: GameSaveBackup[];
}

export interface RpgmSaveSlot {
  file: string;
  slot: number;
  modified: string;
  size: number;
  playtime: string;
  title: string;
}

export interface RpgmSavesListResponse extends ApiBasicResponse {
  slots: RpgmSaveSlot[];
}

export type RpgmInventoryKind = "item" | "weapon" | "armor";

export interface RpgmDatabaseEntry {
  id: number;
  name: string;
  kind: "item" | "key" | "hidden";
  description: string;
}

export interface RpgmNames {
  items: RpgmDatabaseEntry[];
  weapons: RpgmDatabaseEntry[];
  armors: RpgmDatabaseEntry[];
  actors: Record<string, string>;
  classes: Record<string, { name: string; expParams: number[] | null }>;
  switches: string[];
  variables: string[];
  currency: string;
  params: string[];
}

export interface RpgmInventoryRow {
  id: number;
  count: number;
  name: string;
  known: boolean;
}

export interface RpgmActorView {
  id: number;
  name: string;
  database_name: string;
  level: number | null;
  exp: number | null;
  class_id: number | null;
  class_name: string;
  has_curve: boolean;
  hp: number | null;
  mp: number | null;
  param_plus: number[];
}

export interface RpgmValueRow {
  id: number;
  name: string;
  value: boolean | number | string | null;
  type: "bool" | "number" | "string" | "other";
}

export interface RpgmSaveView {
  gold: number | null;
  inventory: Record<RpgmInventoryKind, RpgmInventoryRow[]>;
  actors: RpgmActorView[];
  switches: RpgmValueRow[];
  variables: RpgmValueRow[];
  missing: string[];
}

export type RpgmActorField = "name" | "level" | "exp" | "hp" | "mp" | "paramPlus";

export type RpgmPatch =
  | { op: "set"; path: (string | number)[]; value: boolean | number | string }
  | { op: "set_count"; kind: RpgmInventoryKind; id: number; count: number }
  | { op: "set_switch"; id: number; value: boolean }
  | { op: "set_variable"; id: number; value: number | string }
  | { op: "set_actor"; id: number; field: RpgmActorField; value: number | string; index?: number };

export interface RpgmSaveLoadResponse extends ApiBasicResponse {
  format: "mv" | "mz";
  size: number;
  // a string: nanosecond timestamps exceed JS's safe integer range
  mtime_ns: string;
  view: RpgmSaveView;
  names: RpgmNames;
  raw: Record<string, unknown>;
}

export interface RpgmSaveWriteResponse extends ApiBasicResponse {
  backup?: string;
  message?: string;
  backups?: GameSaveBackup[];
}

export interface StoreLink {
  source: string;
  id: string;
  url: string;
}

export interface NoteLink {
  url: string;
  label: string;
}

export interface GameRecord {
  note_links?: NoteLink[];
  links?: StoreLink[];
  gamemode?: "default" | "on" | "off";
  mangohud?: "default" | "on" | "off";
  gamescope?: "default" | "on" | "off";
  // Per-game gamescope overrides; "" uses the global value.
  gamescope_resolution?: string;
  gamescope_scaler?: "" | GamescopeScaler;
  gamescope_filter?: "" | GamescopeFilter;
  gamescope_args?: string;
  // Where an .html target opens: "" follows the Settings default.
  html_player?: "" | "builtin" | "browser";
  sandbox_enabled?: boolean;
  block_network?: boolean;
  // Set once the user edits the tags; F95 backfill then never refills them.
  tags_user_edited?: boolean;
  // The last F95 tag list wLib saw; null means no baseline yet.
  f95_tags?: string | null;
  id: number;
  title: string;
  exe_path: string;
  f95_url?: string;
  version?: string;
  latest_version?: string;
  cover_image_path?: string;
  cover_local?: string | null;
  cover_local_url?: string;
  cover_cache_url?: string;
  cover_card_still_url?: string;
  cover_card_anim_url?: string;
  cover_animated?: boolean;
  cover_image?: string;
  tags?: string | string[];
  rating?: string;
  // When the F95 rating was read (UTC ISO); "" when unknown.
  rating_updated_at?: string;
  developer?: string;
  engine?: string;
  status?: string;
  play_status?: string;
  finished_version?: string;
  last_played_version?: string;
  skipped_version?: string;
  playtime_seconds?: number;
  // Timestamps are nullable columns: a game that was never played reports
  // null for last_played, and older rows carry no date_added.
  last_played?: string | null;
  date_added?: string | null;
  command_line_args?: string;
  run_japanese_locale?: boolean;
  run_wayland?: boolean;
  auto_inject_ce?: boolean;
  custom_prefix?: string;
  proton_version?: string;
  save_path_override?: string;
  /** Name of the default launch target; empty shows "Default". */
  default_target_label?: string;
  launch_mode?: LaunchMode;
  is_favorite?: boolean;
  rating_graphics?: number;
  rating_story?: number;
  rating_fappability?: number;
  rating_gameplay?: number;
  thread_main_post_last_edit_at?: string | null;
  thread_main_post_checked_at?: string | null;
  thread_completed?: boolean | number;
  launch_targets?: LaunchTarget[];
  // Decoded collection membership; names always match the collection list.
  collections?: string[];
  // Remote screenshot URLs collected from the thread starter post.
  screenshots?: string[];
  // Notes markdown stored in games.progress.
  progress?: string;
  // F95 metadata is still being fetched in the background after adding.
  metadata_pending?: boolean;
}

export interface PlaySession {
  id: number;
  game_id: number;
  version: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number;
}

export interface PlaytimeTickDetail {
  gameId: number;
  delta: number;
  isFinal: boolean;
  lastPlayedVersion: string;
  sessionId: number;
}

export interface RpgmakerLinuxRunnerStatus {
  available: boolean;
  path: string;
  source: string;
  configured_path: string;
  error: string;
}

export type HostPlatform = "linux" | "windows" | "unsupported";

export interface SandboxStatus {
  status: "ready" | "missing" | "unusable";
  tool: string;
  path: string;
  version: string;
  reason: string;
}

export interface SandboxInstallInfo {
  platform: HostPlatform;
  tool: string;
  command: string;
  can_install: boolean;
  pkexec_available: boolean;
  download_url: string;
  package_manager: string;
}

export interface CompanionTool {
  id: number;
  name: string;
  kind: "wine" | "native" | "command";
  path: string;
  arguments: string;
  show_on_all_games: boolean;
  sort_order: number;
}

export type CompanionToolInput = Omit<CompanionTool, "id"> & { id?: number };
export interface GameCompanionTool extends CompanionTool { visible: boolean; overridden: boolean }
export interface CompanionToolsResponse<T = CompanionTool> extends ApiBasicResponse { tools?: T[] }
export interface CompanionToolResponse extends ApiBasicResponse { tool?: CompanionTool }

export interface PlatformCapabilities {
  platform: HostPlatform;
  sandbox: "bwrap" | "sandboxie" | null;
  native_windows_launch: boolean;
  wine_proton: boolean;
  runtime_installers: boolean;
  wayland: boolean;
  gaming_mode: boolean;
  rpgmaker_linux: boolean;
  companion_tools: boolean;
  cheat_engine_injection: boolean;
  launch_modes: LaunchMode[];
  data_dir: string;
  cache_dir: string;
  extension_dir: string;
}

export type TorrentSeedMode = "ask" | "always" | "never";

export type GamescopeWindowMode = "fullscreen" | "borderless" | "windowed";
export type GamescopeScaler = "auto" | "integer" | "fit" | "fill" | "stretch";
export type GamescopeFilter = "linear" | "nearest" | "fsr" | "nis" | "pixel";

/** Gamescope on the host, and the install command when it is missing. */
export interface GamescopeStatus {
  installed: boolean;
  /** Empty when installed, or when no known package manager was found. */
  command: string;
  package_manager: string;
  nixos: boolean;
}

/** Global gamescope settings. Resolutions are `WxH` or "" (Auto); FPS limit "" is Off. */
export interface GamescopeSettings {
  gamescope_enabled: boolean;
  gamescope_window_mode: GamescopeWindowMode;
  gamescope_output_resolution: string;
  gamescope_game_resolution: string;
  gamescope_scaler: GamescopeScaler;
  gamescope_filter: GamescopeFilter;
  gamescope_fps_limit: string;
  gamescope_args: string;
}

export interface SettingsPayload extends Partial<GamescopeSettings> {
  gamemode_enabled?: boolean;
  mangohud_enabled?: boolean;
  html_builtin_player?: boolean;
  web_player_devtools?: boolean;
  sandbox_new_games?: boolean;
  sandboxie_custom_path?: string;
  update_notifications?: boolean;
  torrent_seed_mode?: TorrentSeedMode;
  torrent_seed_ratio?: number | string;
  torrent_seed_hours?: number | string;
  torrent_upload_limit_kib?: number | string;
  download_limit_kib?: number | string;
  download_max_active?: number | string;
  download_pause_while_playing?: boolean;
  download_pause_on_battery?: boolean;
  torrent_port_mapping?: boolean;
  torrent_watch_folder?: string;
  torrent_export_warning_accepted?: boolean;
  custom_play_statuses?: string[];
  collections?: string[];
  library_filter_presets?: LibraryFilterPreset[];
  urm_rpa_path?: string;
  proton_path?: string;
  wine_prefix_path?: string;
  enable_logging?: boolean;
  rpgmaker_linux_runner_path?: string;
  library_folder?: string;
  library_folders?: string[];
  library_folder_pattern?: string;
  downloads_folder?: string;
  download_detection_enabled?: boolean;
  save_backup_keep?: SaveBackupKeep;
  catalog_enabled?: boolean;
}

export interface UrmStatusResponse extends ApiBasicResponse {
  renpy?: boolean;
  installed?: boolean;
  source_configured?: boolean;
}

export type GameToolEngine = "renpy" | "rpgm" | "unity" | "";
export type RenpyConsoleState = "off" | "on" | "developer";
export type RenpyExtractMode = "folder" | "in_place";
export type RpgmKeySource = "system" | "recovered" | "";

export interface GameToolSupportResponse extends ApiBasicResponse {
  engine?: GameToolEngine;
}

export interface RenpyArchive {
  name: string;
  size: number;
  // "RPA-3.0" etc., or "" when the header is not one wLib can read
  format: string;
}

export interface RenpyToolsState {
  console: RenpyConsoleState;
  archives: RenpyArchive[];
  urm: UrmStatusResponse;
}

export interface RpgmToolsState {
  encrypted: number;
  images: number;
  audio: number;
  // encrypted assets without a decrypted copy yet
  missing: number;
  key_source: RpgmKeySource;
  use_decrypted: boolean;
  backup: boolean;
}

export interface UnityBuild { platform: "windows" | "linux"; data_folder: string; backend: "mono" | "il2cpp" | ""; arch: "x86" | "x64" | ""; version: string }
export interface BepInExInstalled { version: string; major: number; backend: "mono" | "il2cpp"; complete: boolean; managed_by_wlib: boolean }
export interface UnityToolsState { build: UnityBuild; platform: "windows" | "linux"; installed: BepInExInstalled | null; has_backup: boolean; unity_libs: boolean }
export interface BepInExBuild { id: string; major: number; backend: "mono" | "il2cpp"; channel: "stable" | "pre" | "be"; date: string }
export interface BepInExVersions extends ApiBasicResponse { builds?: BepInExBuild[]; recommended?: BepInExBuild | null; warnings?: string[] }

export interface GameToolsResponse extends ApiBasicResponse {
  engine?: GameToolEngine;
  renpy?: RenpyToolsState;
  rpgm?: RpgmToolsState;
  unity?: UnityToolsState;
}

export interface GameToolStatus extends ApiBasicResponse {
  running: boolean;
  action: string;
  done: number;
  total: number;
  current: string;
  cancel: boolean;
}

export interface RenpyExtractOptions {
  mode: RenpyExtractMode;
  output?: string;
}

export interface GameToolResult extends ApiBasicResponse {
  message?: string;
  warning?: string;
  cancelled?: boolean;
  // console switch
  state?: RenpyConsoleState;
  // extraction
  files?: number;
  bytes?: number;
  skipped?: number;
  skipped_scripts?: number;
  skipped_existing?: number;
  failed?: number;
  unsupported?: string[];
  // decryption
  decrypted?: number;
  already?: number;
  key_source?: RpgmKeySource;
  use_decrypted?: boolean;
}

export interface SettingsResponse extends GamescopeSettings {
  gamemode_enabled: boolean;
  mangohud_enabled: boolean;
  html_builtin_player: boolean;
  /** F12 DevTools in the built-in player; applies to the next launch. In backups and sync. */
  web_player_devtools: boolean;
  sandbox_new_games: boolean;
  sandboxie_custom_path: string;
  /** After a torrent finishes. In backups and sync. */
  torrent_seed_mode: TorrentSeedMode;
  /** Stop seeding at this upload ratio; 0 means no ratio limit. */
  torrent_seed_ratio: number;
  /** Optional seeding time limit in hours; empty means none. */
  torrent_seed_hours: number | "";
  /** Device-local: KiB/s, 0 means unlimited. */
  torrent_upload_limit_kib: number;
  /** Device-local: the shared download speed limit for file hosts and torrents, KiB/s; 0 means unlimited. */
  download_limit_kib: number;
  /** Device-local: downloads at once, 1 to 5. */
  download_max_active: number;
  /** Device-local: downloads wait while a game started from wLib runs. */
  download_pause_while_playing: boolean;
  /** Device-local: downloads wait while the computer runs on battery. */
  download_pause_on_battery: boolean;
  torrent_port_mapping: boolean;
  /** Device-local: an external client's watch folder; empty means none. */
  torrent_watch_folder: string;
  /** Device-local: the user accepted the one-time passkey warning. */
  torrent_export_warning_accepted: boolean;
  tag_colors: Record<string, string>;
  update_notifications?: boolean;
  custom_play_statuses: string[];
  collections: string[];
  library_filter_presets: LibraryFilterPreset[];
  urm_rpa_path: string;
  proton_path: string;
  wine_prefix_path: string;
  enable_logging: boolean;
  rpgmaker_linux_runner_path: string;
  rpgmaker_linux_runner_status: RpgmakerLinuxRunnerStatus;
  library_folder: string;
  library_folders: string[];
  library_folder_pattern: string;
  downloads_folder: string;
  downloads_folder_exists: boolean;
  download_detection_enabled: boolean;
  save_backup_keep: SaveBackupKeep;
  catalog_enabled: boolean;
}

export interface CatalogSourceStatus {
  count: number | null;
  updated_at: string | null;
}

export interface CatalogStatusResponse extends ApiBasicResponse {
  enabled?: boolean;
  installed?: boolean;
  built_at?: string | null;
  sources?: Record<string, CatalogSourceStatus>;
  last_check?: string | null;
  last_error?: string | null;
  checking?: boolean;
  started?: boolean;
}

export interface CollectionsResponse extends ApiBasicResponse {
  collections?: string[];
}

export interface DiagnosticsBasic {
  app_version: string;
  extension_version: string;
  packaging: string;
  os: string;
  platform: string;
  python: string;
  qt: string | null;
  webengine: string | null;
  renderer_stages: Record<string, Record<string, unknown>>;
  data_path: string;
  database_path: string;
  log_path: string;
  database_bytes: number;
  game_count: number;
}

export interface DiagnosticsTools {
  "7zz": string | null;
  "7z": string | null;
  "7za": string | null;
  bsdtar: string | null;
  unrar: string | null;
  wine?: string | null;
  runners?: RunnerInfo[];
  extension: { success: boolean; reachable: boolean; error?: string; bridge?: ExtensionBridgeStatus };
}

export type PathCheckKind = "file" | "dir" | "parent" | "runner";

export interface PathCheckResponse extends ApiBasicResponse {
  ok?: boolean;
  reason?: string;
}

// The browser mock backend loads on the first call that has no bridge method.
let mockBackendPromise: Promise<MockBackend> | null = null;
const loadMockBackend = () =>
  (mockBackendPromise ??= import("./mock/backend").then((m) => m.mockBackend));

declare global {
  interface Window {
    pywebview?: PyWebViewBridge;
  }
}

class ApiService {
  private gameSaves = new Map<number, Promise<void>>();
  constructor() {
    this.isWebview = window.pywebview !== undefined;
    this._mockWarnings = new Set();
  }

  isWebview: boolean;
  _mockWarnings: Set<string>;
  async invoke<T = unknown>(method: string, ...args: unknown[]): Promise<T> {
    const invoker = window.pywebview?.api?.[method];

    if (invoker) {
      return (await invoker(...args)) as T;
    }

    if (!this._mockWarnings.has(method)) {
      console.warn(`[ApiService] Mocking call to ${method}`, args);
      this._mockWarnings.add(method);
    }

    return (await loadMockBackend()).respond(method, args) as T;
  }

  async getCoverCacheStatus(): Promise<ApiBasicResponse & { count?: number; bytes?: number; running?: boolean }> {
    return this.invoke("get_cover_cache_status");
  }

  async purgeCoverCache(): Promise<ApiBasicResponse & { count?: number; bytes?: number; running?: boolean }> {
    return this.invokeLibraryChange("purge_cover_cache");
  }

  async getGameScreenshots(gameId: number): Promise<GameScreenshotsResponse> {
    return this.invoke<GameScreenshotsResponse>("get_game_screenshots", gameId);
  }

  async getGames(): Promise<GameRecord[]> {
    return this.invoke<GameRecord[]>("get_games");
  }

  async chooseGameCover(gameId: number): Promise<ApiBasicResponse & { cancelled?: boolean; game?: GameRecord }> {
    return this.invokeLibraryChange("choose_game_cover", gameId);
  }

  async setGameCoverFile(gameId: number, path: string): Promise<ApiBasicResponse & { game?: GameRecord }> {
    return this.invokeLibraryChange("set_game_cover_file", gameId, path);
  }

  async clearGameCover(gameId: number): Promise<ApiBasicResponse & { game?: GameRecord }> {
    return this.invokeLibraryChange("clear_game_cover", gameId);
  }

  async getCollections(): Promise<string[]> {
    return this.invoke<string[]>("get_collections");
  }

  async createCollection(name: string, gameId?: number): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "create_collection",
      name,
      gameId ?? null,
    );
  }

  async renameCollection(oldName: string, newName: string): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "rename_collection",
      oldName,
      newName,
    );
  }

  async deleteCollection(name: string): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>("delete_collection", name);
  }

  async reorderCollections(names: string[]): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>("reorder_collections", names);
  }

  async setGameCollections(gameId: number, names: string[]): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "set_game_collections",
      gameId,
      names,
    );
  }

  async saveLibraryFilterPresets(presets: LibraryFilterPreset[]): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("save_library_filter_presets", presets);
  }

  async checkPath(path: string, kind: PathCheckKind): Promise<PathCheckResponse> {
    return this.invoke<PathCheckResponse>("check_path", path, kind);
  }

  private libraryBatchDepth = 0;
  private libraryBatchChanged = false;

  /** Run several library changes with one library refresh at the end. */
  async withLibraryBatch<T>(run: () => Promise<T>): Promise<T> {
    this.libraryBatchDepth++;
    try {
      return await run();
    } finally {
      this.libraryBatchDepth--;
      if (!this.libraryBatchDepth && this.libraryBatchChanged) {
        this.libraryBatchChanged = false;
        window.dispatchEvent(new Event("wlib-refresh-library"));
      }
    }
  }

  private notifyLibraryChange(): void {
    if (this.libraryBatchDepth) this.libraryBatchChanged = true;
    else window.dispatchEvent(new Event("wlib-refresh-library"));
  }

  private async invokeLibraryChange<T extends ApiBasicResponse>(method: string, ...args: unknown[]): Promise<T> {
    const result = await this.invoke<T>(method, ...args);
    if (result?.success) this.notifyLibraryChange();
    return result;
  }

  async addGame(
    title: string,
    exe_path: string,
    f95_url = "",
    version = "",
    cover_image = "",
    tags = "",
    rating = "",
    developer = "",
    engine = "",
    run_japanese_locale = false,
    run_wayland = false,
    auto_inject_ce = false,
    custom_prefix = "",
    proton_version = "",
    launch_mode: LaunchMode = "auto",
    command_line_args = "",
    links: StoreLink[] = [],
  ): Promise<AddGameResponse> {
    return this.invokeLibraryChange(
      "add_game",
      title,
      exe_path,
      f95_url,
      version,
      cover_image,
      tags,
      rating,
      developer,
      engine,
      run_japanese_locale,
      run_wayland,
      auto_inject_ce,
      custom_prefix,
      proton_version,
      launch_mode,
      command_line_args,
      links,
    );
  }

  async deleteGame(id: number): Promise<ApiBasicResponse> {
    return this.invokeLibraryChange("delete_game", id);
  }

  async updateGame(id: number, fields: Record<string, unknown>): Promise<UpdateGameResponse> {
    const previous = this.gameSaves.get(id) ?? Promise.resolve();
    const snapshot = { ...fields };
    const request = previous.then(() => this.invokeLibraryChange("update_game", id, snapshot));
    const settled = request.then(() => {}, () => {});
    this.gameSaves.set(id, settled);
    void settled.then(() => {
      if (this.gameSaves.get(id) === settled) this.gameSaves.delete(id);
    });
    return request;
  }

  async inspectGamePath(exePath: string): Promise<InspectGamePathResponse> {
    return this.invoke<InspectGamePathResponse>("inspect_game_path", exePath);
  }

  async scanGamesFolder(folder: string): Promise<ScanGamesFolderResponse> {
    return this.invoke<ScanGamesFolderResponse>("scan_games_folder", folder);
  }

  // Adds each game without a library refresh per game, then refreshes once.
  async importGames(games: GameGuess[]): Promise<{ added: number; errors: string[] }> {
    let added = 0;
    const errors: string[] = [];
    for (const game of games) {
      try {
        const result = await this.invoke<AddGameResponse>(
          "add_game", game.title, game.exe_path, "", game.version, "", "", "", "", game.engine,
        );
        if (result?.success) added++;
        else errors.push(`${game.title}: ${result?.error || "Unknown error"}`);
      } catch (error) {
        errors.push(`${game.title}: ${String(error)}`);
      }
    }
    if (added) window.dispatchEvent(new Event("wlib-refresh-library"));
    return { added, errors };
  }

  async markGameUpdated(game: GameRecord): Promise<ApiBasicResponse> {
    if (!hasAvailableUpdate(game)) return { success: false, error: "No newer version is available." };
    return this.updateGame(game.id, { version: String(game.latest_version).trim() });
  }

  async openExtensionFolder(): Promise<ExtensionSyncStatus> {
    return this.invoke<ExtensionSyncStatus>("open_extension_folder");
  }

  async getExtensionServiceStatus(): Promise<ExtensionServiceStatus> {
    return this.invoke<ExtensionServiceStatus>("get_extension_service_status");
  }

  async getDownloadAccounts(): Promise<DownloadAccounts> { return this.invoke<DownloadAccounts>("get_download_accounts"); }
  async connectPixeldrainAccount(apiKey: string): Promise<PixeldrainConnectResponse> { return this.invoke("connect_pixeldrain_account", apiKey); }
  async disconnectPixeldrainAccount(): Promise<ApiBasicResponse> { return this.invoke("disconnect_pixeldrain_account"); }
  async connectMegaAccount(email: string, password: string, code = ""): Promise<MegaConnectResponse> { return this.invoke("connect_mega_account", email, password, code); }
  async disconnectMegaAccount(): Promise<ApiBasicResponse> { return this.invoke("disconnect_mega_account"); }
  async connectRpdlAccount(username: string, password: string): Promise<RpdlConnectResponse> { return this.invoke("connect_rpdl_account", username, password); }
  async disconnectRpdlAccount(): Promise<ApiBasicResponse> { return this.invoke("disconnect_rpdl_account"); }

  async getSteamStatus(): Promise<SteamStatus> { return this.invoke<SteamStatus>("get_steam_status"); }
  async addWlibToSteam(): Promise<ApiBasicResponse & {pending?: boolean; already_exists?: boolean; queued?: boolean}> { return this.invoke("add_wlib_to_steam"); }
  async setSteamGamesMode(mode: SteamMode): Promise<ApiBasicResponse> { return this.invoke("set_steam_games_mode", mode); }
  async setSteamAccount(account: string): Promise<ApiBasicResponse> { return this.invoke("set_steam_account", account); }
  async syncSteamShortcuts(): Promise<ApiBasicResponse> { return this.invoke("sync_steam_shortcuts"); }
  async setGameSteamShortcut(id: number, marked: boolean): Promise<ApiBasicResponse> { return this.invoke("set_game_steam_shortcut", id, marked); }
  async getGameSteamState(id: number): Promise<SteamGameState> { return this.invoke("get_game_steam_state", id); }

  async getSteamCoverConversions(): Promise<ApiBasicResponse & { covers?: SteamCoverConversion[] }> {
    return this.invoke("get_steam_cover_conversions");
  }
  async saveSteamCoverPng(token: string, data: string): Promise<ApiBasicResponse> {
    return this.invoke("save_steam_cover_png", token, data);
  }

  async getExtensionPairing(): Promise<ExtensionPairingResponse> {
    return this.invoke<ExtensionPairingResponse>("get_extension_pairing");
  }

  async resolveExtensionPairing(requestId: string, allow: boolean): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("resolve_extension_pairing", requestId, allow);
  }

  async revokeExtensionPairing(pairingId: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("revoke_extension_pairing", pairingId);
  }

  async getStartupLaunchError(): Promise<{ error: string }> {
    return this.invoke<{ error: string }>("get_startup_launch_error");
  }

  async getStartupExtensionSyncStatus(): Promise<ExtensionSyncStatus> {
    return this.invoke<ExtensionSyncStatus>("get_startup_extension_sync_status");
  }

  async openInBrowser(url: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_in_browser", url);
  }

  async checkForUpdates(url: string): Promise<UpdateCheckResponse> {
    return this.invokeLibraryChange<UpdateCheckResponse>("check_for_updates", url);
  }

  async checkAllUpdates(gameIds?: number[], options?: { refreshInfo?: boolean }): Promise<BulkUpdateStartResponse> {
    if (options?.refreshInfo) return this.invoke<BulkUpdateStartResponse>("check_all_updates", gameIds ?? null, true);
    return gameIds === undefined ? this.invoke<BulkUpdateStartResponse>("check_all_updates") : this.invoke<BulkUpdateStartResponse>("check_all_updates", gameIds);
  }

  async getUpdateStatus(): Promise<UpdateStatusResponse> {
    return this.invoke<UpdateStatusResponse>("get_update_status");
  }

  async cancelUpdateCheck(): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_update_check");
  }

  async getAutoCheckSetting(): Promise<AutoCheckSettingResponse> {
    return this.invoke<AutoCheckSettingResponse>("get_auto_check_setting");
  }

  async getGamePlaySessions(gameId: number): Promise<ApiBasicResponse & { sessions?: PlaySession[] }> {
    return this.invoke("get_game_play_sessions", gameId);
  }

  async setWindowFocused(focused: boolean): Promise<ApiBasicResponse> {
    return this.invoke("set_window_focused", focused);
  }

  async setAutoCheckSetting(frequency: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("set_auto_check_setting", frequency);
  }

  async maybeAutoCheck(): Promise<MaybeAutoCheckResponse> {
    return this.invoke<MaybeAutoCheckResponse>("maybe_auto_check");
  }

  async launchGame(
    game_id: number,
    exe_path: string,
    command_line_args = "",
    run_japanese_locale = false,
    run_wayland = false,
    auto_inject_ce = false,
    custom_prefix = "",
    proton_version = "",
    launch_mode: LaunchMode = "auto"
  ): Promise<LaunchGameResponse> {
    return this.invoke(
      "launch_game",
      game_id,
      exe_path,
      command_line_args,
      run_japanese_locale,
      run_wayland,
      auto_inject_ce,
      custom_prefix,
      proton_version,
      launch_mode,
    );
  }

  async getAvailableRunners(): Promise<GetAvailableRunnersResponse> {
    return this.invoke<GetAvailableRunnersResponse>("get_available_runners");
  }

  async stopGame(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("stop_game", gameId);
  }

  async getRunningGames(): Promise<number[]> {
    return this.invoke<number[]>("get_running_games");
  }

  async getUrmStatus(gameId: number): Promise<UrmStatusResponse> {
    return this.invoke<UrmStatusResponse>("get_urm_status", gameId);
  }

  async setUrmInstalled(gameId: number, installed: boolean): Promise<UrmStatusResponse> {
    return this.invoke<UrmStatusResponse>("set_urm_installed", gameId, installed);
  }

  async browseUrmFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_urm_file", startPath);
  }

  async getGameToolSupport(gameId: number): Promise<GameToolSupportResponse> {
    return this.invoke<GameToolSupportResponse>("get_game_tool_support", gameId);
  }

  async getGameTools(gameId: number): Promise<GameToolsResponse> {
    return this.invoke<GameToolsResponse>("get_game_tools", gameId);
  }

  async listBepinexVersions(gameId: number): Promise<BepInExVersions> {
    return this.invoke<BepInExVersions>("list_bepinex_versions", gameId);
  }
  async installBepinex(gameId: number, buildId: string): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("install_bepinex", gameId, { build_id: buildId });
  }
  async uninstallBepinex(gameId: number, removeUserData: boolean): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("uninstall_bepinex", gameId, { remove_user_data: removeUserData });
  }
  async restoreBepinex(gameId: number): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("restore_bepinex", gameId);
  }

  async setRenpyConsole(gameId: number, enabled: boolean, developer: boolean): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("set_renpy_console", gameId, enabled, developer);
  }

  async extractRenpyArchives(gameId: number, options: RenpyExtractOptions): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("extract_renpy_archives", gameId, options);
  }

  async decryptRpgmAssets(gameId: number): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("decrypt_rpgm_assets", gameId);
  }

  async setRpgmUseDecrypted(gameId: number, enabled: boolean): Promise<GameToolResult> {
    return this.invoke<GameToolResult>("set_rpgm_use_decrypted", gameId, enabled);
  }

  async getGameToolStatus(gameId: number): Promise<GameToolStatus> {
    return this.invoke<GameToolStatus>("get_game_tool_status", gameId);
  }

  async cancelGameTool(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_game_tool", gameId);
  }

  /** Converts the game's videos in place in the background; poll getWebMediaConversionStatus. */
  async startWebMediaConversion(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("start_web_media_conversion", gameId);
  }

  async cancelWebMediaConversion(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_web_media_conversion", gameId);
  }

  async getWebMediaConversionStatus(gameId: number): Promise<WebMediaStatus> {
    return this.invoke<WebMediaStatus>("get_web_media_conversion_status", gameId);
  }

  /** Starts the built-in player without the video dialog until the game's videos change. */
  async setWebMediaPlayAnyway(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("set_web_media_play_anyway", gameId);
  }

  async getExecutableModifiedTime(exe_path: string): Promise<ExecutableModifiedTimeResponse> {
    return this.invoke<ExecutableModifiedTimeResponse>("get_executable_modified_time", exe_path);
  }

  async listCompanionTools(): Promise<CompanionToolsResponse> {
    return this.invoke("list_companion_tools");
  }
  async saveCompanionTool(tool: CompanionToolInput): Promise<CompanionToolResponse> {
    return this.invoke("save_companion_tool", tool);
  }
  async deleteCompanionTool(toolId: number): Promise<ApiBasicResponse> {
    return this.invoke("delete_companion_tool", toolId);
  }
  async getGameCompanionTools(gameId: number): Promise<CompanionToolsResponse<GameCompanionTool>> {
    return this.invoke("get_game_companion_tools", gameId);
  }
  async setGameCompanionToolOverride(gameId: number, toolId: number, visible: boolean | null): Promise<ApiBasicResponse> {
    return this.invoke("set_game_companion_tool_override", gameId, toolId, visible);
  }
  async listLaunchableCompanionTools(gameId: number): Promise<CompanionToolsResponse> {
    return this.invoke("list_launchable_companion_tools", gameId);
  }
  async launchCompanionTool(gameId: number, toolId: number): Promise<ApiBasicResponse> {
    return this.invoke("launch_companion_tool", gameId, toolId);
  }

  async getLaunchTargets(game_id: number): Promise<LaunchTarget[]> {
    return this.invoke<LaunchTarget[]>("get_launch_targets", game_id);
  }

  async createLaunchTarget(
    game_id: number,
    label: string,
    exe_path: string,
    sort_order: number | null = null
  ): Promise<LaunchTargetResponse> {
    return this.invoke<LaunchTargetResponse>(
      "create_launch_target",
      game_id,
      label,
      exe_path,
      sort_order,
    );
  }

  async updateLaunchTarget(
    target_id: number,
    fields: Partial<Pick<LaunchTarget, "label" | "exe_path" | "sort_order">>
  ): Promise<LaunchTargetResponse> {
    return this.invoke<LaunchTargetResponse>("update_launch_target", target_id, fields);
  }

  async deleteLaunchTarget(target_id: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("delete_launch_target", target_id);
  }

  async reorderLaunchTargets(
    game_id: number,
    target_ids: number[]
  ): Promise<LaunchTargetsResponse> {
    return this.invoke<LaunchTargetsResponse>(
      "reorder_launch_targets",
      game_id,
      target_ids,
    );
  }

  async getSettings(): Promise<SettingsResponse> {
    return this.invoke<SettingsResponse>("get_settings");
  }

  async setTagColor(tag: string, swatch: string | null): Promise<ApiBasicResponse & { tag_colors?: Record<string, string> }> {
    return this.invoke("set_tag_color", tag, swatch);
  }

  async getDiagnostics(): Promise<ApiBasicResponse & { basic?: DiagnosticsBasic; report_basic?: string }> {
    return this.invoke("get_diagnostics");
  }

  async getDiagnosticsTools(): Promise<ApiBasicResponse & { tools?: DiagnosticsTools }> {
    return this.invoke("get_diagnostics_tools");
  }

  async getDiagnosticsReport(): Promise<ApiBasicResponse & { report?: string }> {
    return this.invoke("get_diagnostics_report");
  }

  async openLogFolder(): Promise<ApiBasicResponse> {
    return this.invoke("open_log_folder");
  }

  async getPerformanceTools(refresh = false): Promise<{ gamemode: boolean; mangohud: boolean; gamescope: boolean }> {
    return this.invoke("get_performance_tools", refresh);
  }

  async getGamescopeStatus(refresh = false): Promise<GamescopeStatus> {
    return this.invoke<GamescopeStatus>("get_gamescope_status", refresh);
  }

  /** `gameId` targets that game's prefix (its own one when sandboxed) when `prefix` is empty. */
  async openWinePrefixTool(tool: "winecfg" | "winetricks", prefix: string, runner: string, gameId: number | null = null): Promise<ApiBasicResponse> {
    return this.invoke("open_wine_prefix_tool", tool, prefix, runner, gameId);
  }

  async getSandboxStatus(): Promise<SandboxStatus> {
    return this.invoke<SandboxStatus>("get_sandbox_status");
  }

  async recheckSandboxStatus(): Promise<SandboxStatus> {
    return this.invoke<SandboxStatus>("recheck_sandbox_status");
  }

  async getSandboxInstallInfo(): Promise<SandboxInstallInfo> {
    return this.invoke<SandboxInstallInfo>("get_sandbox_install_info");
  }

  async installSandboxTool(tool: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("install_sandbox_tool", tool);
  }

  async getSandboxInstallStatus(): Promise<InstallProgressStatus> {
    return this.invoke<InstallProgressStatus>("get_sandbox_install_status");
  }

  async setSandboxForAllGames(enabled: boolean): Promise<ApiBasicResponse & {changed?: number}> {
    return this.invokeLibraryChange("set_sandbox_for_all_games", enabled);
  }

  async openGameSandboxFolder(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_game_sandbox_folder", gameId);
  }

  async openSteamKeyboard(): Promise<{ success: boolean; code?: string; error?: string }> {
    return this.invoke("open_steam_keyboard");
  }

  async getPlatformCapabilities(): Promise<PlatformCapabilities> {
    return this.invoke<PlatformCapabilities>("get_platform_capabilities");
  }

  /** On a failure, `field` names the setting that was refused (gamescope fields). */
  async saveSettings(settings: SettingsPayload): Promise<ApiBasicResponse & { field?: string }> {
    return this.invoke<ApiBasicResponse & { field?: string }>("save_settings", settings);
  }

  async getCatalogStatus(): Promise<CatalogStatusResponse> {
    return this.invoke<CatalogStatusResponse>("get_catalog_status");
  }

  async checkCatalogNow(): Promise<CatalogStatusResponse> {
    return this.invoke<CatalogStatusResponse>("check_catalog_now");
  }

  async exportLibraryBackup(
    options: LibraryBackupOptions,
    destinationPath: string,
    passphrase: string | null = null,
  ): Promise<LibraryBackupExportResponse> {
    return this.invoke<LibraryBackupExportResponse>(
      "export_library_backup",
      options,
      destinationPath,
      passphrase,
    );
  }

  async inspectLibraryBackup(path: string, passphrase: string | null = null): Promise<LibraryBackupInspectResponse> {
    return this.invoke<LibraryBackupInspectResponse>("inspect_library_backup", path, passphrase);
  }

  async importLibraryBackup(
    path: string,
    options: LibraryBackupOptions,
    passphrase: string | null = null,
  ): Promise<LibraryBackupImportResponse> {
    return this.invoke<LibraryBackupImportResponse>(
      "import_library_backup",
      path,
      options,
      passphrase,
    );
  }

  async detectExternalLibrary(source: ExternalLibrarySource): Promise<ExternalLibraryDetectResponse> {
    return this.invoke<ExternalLibraryDetectResponse>("detect_external_library", source);
  }

  async inspectExternalLibrary(
    source: ExternalLibrarySource,
    path: string,
  ): Promise<ExternalLibraryInspectResponse> {
    return this.invoke<ExternalLibraryInspectResponse>("inspect_external_library", source, path);
  }

  async importExternalLibrary(
    source: ExternalLibrarySource,
    path: string,
    options: ExternalLibraryImportOptions,
  ): Promise<ExternalLibraryImportResponse> {
    return this.invoke<ExternalLibraryImportResponse>(
      "import_external_library",
      source,
      path,
      options,
    );
  }

  async browseExternalLibraryFile(source: ExternalLibrarySource, startPath = ""): Promise<string> {
    return this.invoke<string>("browse_external_library_file", source, startPath);
  }

  async getSyncStatus(): Promise<LibrarySyncStatus> { return this.invoke("get_sync_status"); }
  async setSyncFolder(path: string): Promise<LibrarySyncStatus> { return this.invoke("set_sync_folder", path); }
  async connectDropbox(): Promise<LibrarySyncStatus> { return this.invoke("connect_dropbox"); }
  async connectGdrive(): Promise<LibrarySyncStatus> { return this.invoke("connect_gdrive"); }
  async disconnectSync(): Promise<LibrarySyncStatus> { return this.invoke("disconnect_sync"); }
  async syncNow(): Promise<LibrarySyncStatus> { return this.invoke("sync_now"); }
  async pauseSync(): Promise<LibrarySyncStatus> { return this.invoke("pause_sync"); }
  async resumeSync(): Promise<LibrarySyncStatus> { return this.invoke("resume_sync"); }
  async listSyncSnapshots(): Promise<SyncSnapshotList> { return this.invoke("list_sync_snapshots"); }
  async restoreSyncSnapshot(name: string): Promise<SyncSnapshotRestore> {
    const result = await this.invoke<SyncSnapshotRestore>("restore_sync_snapshot", name);
    if (result.success) this.notifyLibraryChange();
    return result;
  }
  async resolveSyncChoice(choice: LibrarySyncChoice): Promise<LibrarySyncStatus> { return this.invoke("resolve_sync_choice", choice); }
  async setSyncPassphrase(passphrase: string | null): Promise<LibrarySyncStatus> { return this.invoke("set_sync_passphrase", passphrase); }
  async unlockSync(passphrase: string): Promise<LibrarySyncStatus> { return this.invoke("unlock_sync", passphrase); }

  async browseFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_file", startPath);
  }

  async browseRunnerFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_runner_file", startPath);
  }

  async browseBackupFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_backup_file", startPath);
  }

  async browseAttachmentFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_attachment_file", startPath);
  }

  async listGameAttachments(gameId: number): Promise<GameAttachmentsResponse> {
    return this.invoke<GameAttachmentsResponse>("list_game_attachments", gameId);
  }

  async addGameAttachment(
    gameId: number,
    sourcePath: string,
  ): Promise<AddGameAttachmentResponse> {
    return this.invoke<AddGameAttachmentResponse>(
      "add_game_attachment",
      gameId,
      sourcePath,
    );
  }

  async openGameAttachment(gameId: number, name: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_game_attachment", gameId, name);
  }

  async removeGameAttachment(
    gameId: number,
    name: string,
  ): Promise<RemoveGameAttachmentResponse> {
    return this.invoke<RemoveGameAttachmentResponse>(
      "remove_game_attachment",
      gameId,
      name,
    );
  }

  async browseDirectory(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_directory", startPath);
  }

  async browseArchiveFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_archive_file", startPath);
  }

  async lookupThreadDetails(url: string): Promise<ThreadDetailsResponse> {
    return this.invoke<ThreadDetailsResponse>("lookup_thread_details", url);
  }

  async previewInstallDestination(options: InstallDestinationOptions): Promise<InstallDestinationResponse> {
    return this.invoke<InstallDestinationResponse>("preview_install_destination", options);
  }

  async listLibraryFolders(): Promise<LibraryFolder[]> {
    return this.invoke<LibraryFolder[]>("list_library_folders");
  }

  async planGameUninstall(gameIds: number[], rootOverrides: Record<string, string> = {}): Promise<GameFilePlanResponse> {
    return this.invoke<GameFilePlanResponse>("plan_game_uninstall", gameIds, rootOverrides);
  }

  async uninstallGames(gameIds: number[], options: UninstallOptions): Promise<UninstallGamesResponse> {
    const result = await this.invoke<UninstallGamesResponse>("uninstall_games", gameIds, options);
    if (result?.uninstalled) this.notifyLibraryChange();
    return result;
  }

  async planGameMove(gameIds: number[], destParent: string, rootOverrides: Record<string, string> = {}): Promise<GameFilePlanResponse> {
    return this.invoke<GameFilePlanResponse>("plan_game_move", gameIds, destParent, rootOverrides);
  }

  async startGameMove(gameIds: number[], destParent: string, rootOverrides: Record<string, string> = {}): Promise<StartMoveResponse> {
    return this.invoke<StartMoveResponse>("start_game_move", gameIds, destParent, rootOverrides);
  }

  async getMoveStatus(): Promise<MoveStatus> {
    return this.invoke<MoveStatus>("get_move_status");
  }

  async cancelGameMove(cancelAll = false): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_game_move", cancelAll);
  }

  async getUpdateDownloadLinks(gameId: number): Promise<UpdateDownloadLinks> {
    return this.invoke<UpdateDownloadLinks>("get_update_download_links", gameId);
  }
  async closeUpdateDownloadLinks(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("close_update_download_links", gameId);
  }
  /** Adds the mirror to the download queue; `replaces` is a failed download's id. */
  async startUpdateDownload(gameId: number, linkId: string, replaces: string | null = null): Promise<ApiBasicResponse & {job_id?: string}> {
    return this.invoke<ApiBasicResponse & {job_id?: string}>("start_update_download", gameId, linkId, replaces);
  }
  async getDownloadQueue(includeHistory = false): Promise<DownloadQueueStatus> {
    return this.invoke<DownloadQueueStatus>("get_download_queue", includeHistory);
  }
  async reorderDownloads(jobIds: string[]): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("reorder_downloads", jobIds);
  }
  /** outcome: "started", "yielded" (a running download gave up its slot) or "first" (next in line). */
  async startDownloadNow(jobId: string): Promise<StartDownloadNowResponse> {
    return this.invoke<StartDownloadNowResponse>("start_download_now", jobId);
  }
  async pauseDownload(jobId: string): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("pause_download", jobId); }
  async resumeDownload(jobId: string): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("resume_download", jobId); }
  async pauseAllDownloads(): Promise<ApiBasicResponse & {count?: number}> { return this.invoke<ApiBasicResponse & {count?: number}>("pause_all_downloads"); }
  async resumeAllDownloads(): Promise<ApiBasicResponse & {count?: number}> { return this.invoke<ApiBasicResponse & {count?: number}>("resume_all_downloads"); }
  async retryDownload(jobId: string): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("retry_download", jobId); }
  async removeDownload(jobId: string): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("remove_download", jobId); }
  /** Lets downloads run until the current hold (game or battery) ends. */
  async resumeDownloadsNow(): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("resume_downloads_now"); }
  async findRpdlTorrents(gameId: number, version = "", refresh = false): Promise<RpdlSearchResponse> {
    return this.invoke<RpdlSearchResponse>("find_rpdl_torrents", gameId, version, refresh);
  }
  async startRpdlDownload(gameId: number, torrentId: number, version = "", replaces: string | null = null): Promise<ApiBasicResponse & {job_id?: string}> {
    return this.invoke<ApiBasicResponse & {job_id?: string}>("start_rpdl_download", gameId, torrentId, version, replaces);
  }
  async saveRpdlTorrent(torrentId: number, folder: string): Promise<RpdlTorrentExportResponse> {
    return this.invoke<RpdlTorrentExportResponse>("save_rpdl_torrent", torrentId, folder);
  }
  async getRpdlMagnet(torrentId: number): Promise<RpdlMagnetResponse> {
    return this.invoke<RpdlMagnetResponse>("get_rpdl_magnet", torrentId);
  }
  async sendRpdlTorrentToWatchFolder(torrentId: number): Promise<RpdlTorrentExportResponse> {
    return this.invoke<RpdlTorrentExportResponse>("send_rpdl_torrent_to_watch_folder", torrentId);
  }
  async answerSeedPrompt(jobId: string, seed: boolean, remember = false): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("answer_seed_prompt", jobId, seed, remember);
  }
  async stopSeeding(jobId: string): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("stop_seeding", jobId); }
  async openUpdateDownloadInBrowser(gameId: number, linkId: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_update_download_in_browser", gameId, linkId);
  }

  async inspectArchive(archivePath: string, gameId: number | null = null, targetId: number | null = null, libraryFolder: string | null = null): Promise<ArchiveInspectResponse> {
    return this.invoke<ArchiveInspectResponse>("inspect_archive", archivePath, gameId, targetId, libraryFolder);
  }

  async installArchive(options: ArchiveInstallOptions): Promise<ArchiveInstallResponse> {
    return this.invokeLibraryChange<ArchiveInstallResponse>("install_archive", options);
  }

  async releaseDownloadArchive(path: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("release_download_archive", path);
  }

  async getArchiveInstallStatus(): Promise<ArchiveInstallStatus> {
    return this.invoke<ArchiveInstallStatus>("get_archive_install_status");
  }

  async cancelArchiveInstall(): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_archive_install");
  }

  async findDownloadOffers(): Promise<DownloadOffersResponse> {
    return this.invoke<DownloadOffersResponse>("find_download_offers");
  }

  async dismissDownloadOffer(key: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("dismiss_download_offer", key);
  }

  async installRpgmakerDependencies(
    prefix_path: string | null = null,
    proton_path: string | null = null
  ): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>(
      "install_rpgmaker_dependencies",
      prefix_path,
      proton_path,
    );
  }

  async installRpgmakerRtp(
    prefix_path: string | null = null,
    proton_path: string | null = null,
    game_id: number | null = null,
  ): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("install_rpgmaker_rtp", prefix_path, proton_path, game_id);
  }

  async downloadProtonGe(): Promise<DownloadProtonResponse> {
    return this.invoke<DownloadProtonResponse>("download_proton_ge");
  }

  async getUmuStatus(): Promise<UmuStatus> { return this.invoke<UmuStatus>("get_umu_status"); }
  async downloadUmu(): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("download_umu"); }
  async getUmuDownloadStatus(): Promise<UmuJobStatus> { return this.invoke<UmuJobStatus>("get_umu_download_status"); }
  async prepareUmuRuntime(): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("prepare_umu_runtime"); }
  async getUmuRuntimeStatus(): Promise<UmuJobStatus> { return this.invoke<UmuJobStatus>("get_umu_runtime_status"); }
  async cancelUmuRuntimePrepare(): Promise<ApiBasicResponse> { return this.invoke<ApiBasicResponse>("cancel_umu_runtime_prepare"); }

  async getProtonDownloadStatus(): Promise<ProtonDownloadStatus> {
    return this.invoke<ProtonDownloadStatus>("get_proton_download_status");
  }

  async openDevTools(): Promise<void> {
    await this.invoke<void>("open_dev_tools");
  }

  async check_app_updates(): Promise<AppUpdateResponse> {
    return this.invoke<AppUpdateResponse>("check_app_updates");
  }

  async get_app_version(): Promise<AppVersionResponse> {
    return this.invoke<AppVersionResponse>("get_app_version");
  }

  async isCheatEngineInstalled(): Promise<CheatEngineStatusResponse> {
    return this.invoke<CheatEngineStatusResponse>("is_cheat_engine_installed");
  }

  async downloadCheatEngine(): Promise<DownloadCheatEngineResponse> {
    return this.invoke<DownloadCheatEngineResponse>("download_cheat_engine");
  }

  async getInstallStatus(
    prefix_path: string | null = null,
    proton_path: string | null = null
  ): Promise<InstallStatusResponse> {
    return this.invoke<InstallStatusResponse>("get_install_status", prefix_path, proton_path);
  }

  async findSaveFiles(
    exe_path: string,
    title = "",
    engine = "",
    custom_prefix = "",
    proton_version = ""
  ): Promise<SaveLocation[]> {
    return this.invoke<SaveLocation[]>(
      "find_save_files",
      exe_path,
      title,
      engine,
      custom_prefix,
      proton_version,
    );
  }

  async openFolder(path: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_folder", path);
  }

  async getGameSaves(gameId: number): Promise<GameSavesResponse | ApiErrorResult> {
    return this.invoke<GameSavesResponse | ApiErrorResult>("get_game_saves", gameId);
  }

  async setGameSaveFolder(gameId: number, path: string): Promise<GameSaveFolderResponse> {
    return this.invoke<GameSaveFolderResponse>("set_game_save_folder", gameId, path);
  }

  async backupGameSaves(gameId: number): Promise<GameSaveActionResponse> {
    return this.invoke<GameSaveActionResponse>("backup_game_saves", gameId);
  }

  async importGameSaves(gameId: number, source: string): Promise<GameSaveActionResponse> {
    return this.invoke<GameSaveActionResponse>("import_game_saves", gameId, source);
  }

  async restoreGameSavesBackup(gameId: number, backupName: string): Promise<GameSaveActionResponse> {
    return this.invoke<GameSaveActionResponse>("restore_game_saves_backup", gameId, backupName);
  }

  async listRpgmSaves(gameId: number): Promise<RpgmSavesListResponse | ApiErrorResult> {
    return this.invoke<RpgmSavesListResponse | ApiErrorResult>("list_rpgm_saves", gameId);
  }

  async loadRpgmSave(gameId: number, file: string): Promise<RpgmSaveLoadResponse | ApiErrorResult> {
    return this.invoke<RpgmSaveLoadResponse | ApiErrorResult>("load_rpgm_save", gameId, file);
  }

  async writeRpgmSave(
    gameId: number,
    file: string,
    patches: RpgmPatch[],
    size: number,
    mtimeNs: string,
  ): Promise<RpgmSaveWriteResponse | ApiErrorResult> {
    return this.invoke<RpgmSaveWriteResponse | ApiErrorResult>("write_rpgm_save", gameId, file, patches, size, mtimeNs);
  }

  async openGameSaveBackup(gameId: number, backupName: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_game_save_backup", gameId, backupName);
  }

  async getSystemDepsCommand(): Promise<SystemDepsCommandResponse> {
    return this.invoke<SystemDepsCommandResponse>("get_system_deps_command");
  }
}

export const api = new ApiService();

// pywebview creates window.pywebview with an empty api object before
// `_createApi` runs, so the object alone does not mean the bridge is ready.
const bridgeIsReady = (): boolean => {
  const api = window.pywebview?.api;
  return !!api && Object.keys(api).length > 0;
};

// Let PyWebview inject before we consider it fully ready, though in Vue we usually just call it on mount
export function onWebviewReady(callback: () => void): void {
  if (bridgeIsReady()) {
    callback();
  } else if (window.pywebview || /QtWebEngine\//.test(window.navigator?.userAgent ?? "")) {
    // Desktop shell (always gui="qt"): the API is injected after load, so wait
    // instead of running once against mocks and again for real.
    window.addEventListener("pywebviewready", callback, { once: true });
  } else {
    // Plain browser (UI-only dev): pywebviewready never fires, so use mocks now.
    callback();
  }
}
