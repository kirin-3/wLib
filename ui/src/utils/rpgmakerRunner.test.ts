// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import type {
  RpgmakerLinuxRunnerStatus,
  RpgmakerRunnerInstallStatus,
} from "../services/api.ts";
import {
  formatRpgmakerInstallStage,
  getRpgmakerRunnerUiState,
} from "./rpgmakerRunner.ts";

const baseStatus = (
  overrides: Partial<RpgmakerLinuxRunnerStatus> = {},
): RpgmakerLinuxRunnerStatus => ({
  available: false,
  path: "",
  source: "",
  configured_path: "",
  error: "RPGMaker Linux runner is not installed or configured.",
  mkxpz: { installed: false, path: "" },
  installable: {
    supported: true,
    can_install: true,
    missing_tools: [],
    reason: "",
  },
  ...overrides,
});

const baseInstall = (
  overrides: Partial<RpgmakerRunnerInstallStatus> = {},
): RpgmakerRunnerInstallStatus => ({
  success: true,
  running: false,
  target: "",
  stage: "",
  done: 0,
  total: 0,
  error: "",
  ...overrides,
});

test("shows runner Install only when runner is missing and installable, and hides mkxp-z Install until runner is detected", () => {
  const ui = getRpgmakerRunnerUiState(baseStatus(), baseInstall());
  assert.equal(ui.showRunnerInstall, true);
  assert.equal(ui.showRunnerMissingToolsNote, false);
  assert.equal(ui.showMkxpzInstall, false);
  assert.equal(ui.mkxpzInstalled, false);
});

test("hides runner Install and shows mkxp-z Install once runner is detected on x86_64", () => {
  const ui = getRpgmakerRunnerUiState(
    baseStatus({ available: true, path: "/home/u/.local/bin/rpgmaker-linux", error: "" }),
    baseInstall(),
  );
  assert.equal(ui.showRunnerInstall, false);
  assert.equal(ui.showMkxpzInstall, true);
});

test("hides both Install actions when both runner and mkxp-z are installed", () => {
  const ui = getRpgmakerRunnerUiState(
    baseStatus({
      available: true,
      path: "/home/u/.local/bin/rpgmaker-linux",
      error: "",
      mkxpz: { installed: true, path: "/home/u/desktopapps/mkxp-z/mkxp-z.x86_64" },
    }),
    baseInstall(),
  );
  assert.equal(ui.showRunnerInstall, false);
  assert.equal(ui.showMkxpzInstall, false);
  assert.equal(ui.mkxpzInstalled, true);
  assert.match(ui.mkxpzStatusText, /mkxp-z\.x86_64/);
});

test("hides Install actions on non-x86_64 Linux hosts", () => {
  const unsupported = {
    supported: false,
    can_install: false,
    missing_tools: [],
    reason: "Automatic installation is only available on x86_64 Linux.",
  };
  const missingRunner = getRpgmakerRunnerUiState(
    baseStatus({ installable: unsupported }),
    baseInstall(),
  );
  assert.equal(missingRunner.showRunnerInstall, false);
  assert.equal(missingRunner.showRunnerMissingToolsNote, false);
  assert.equal(missingRunner.showMkxpzInstall, false);

  const hasRunner = getRpgmakerRunnerUiState(
    baseStatus({ available: true, path: "/usr/bin/rpgmaker-linux", installable: unsupported }),
    baseInstall(),
  );
  assert.equal(hasRunner.showMkxpzInstall, false);
});

test("names missing host tools when x86_64 lacks bash or file", () => {
  const ui = getRpgmakerRunnerUiState(
    baseStatus({
      installable: {
        supported: true,
        can_install: false,
        missing_tools: ["file"],
        reason: "Missing required host command: file.",
      },
    }),
    baseInstall(),
  );
  assert.equal(ui.showRunnerInstall, false);
  assert.equal(ui.showRunnerMissingToolsNote, true);
  assert.match(ui.runnerMissingToolsReason, /file/);
});

test("disables installs and surfaces stage while running, and error once stopped", () => {
  const running = getRpgmakerRunnerUiState(
    baseStatus(),
    baseInstall({
      running: true,
      target: "runner",
      stage: "Downloading RPGMaker Linux runner v1.1.9",
      done: 50 * 1024 * 1024,
      total: 220 * 1024 * 1024,
    }),
  );
  assert.equal(running.installing, true);
  assert.equal(running.installDisabled, true);
  assert.equal(running.stageText, "Downloading RPGMaker Linux runner v1.1.9 (50 / 220 MB)");
  assert.equal(running.errorText, "");

  const failed = getRpgmakerRunnerUiState(
    baseStatus(),
    baseInstall({
      running: false,
      target: "runner",
      error: "SHA-256 hash mismatch",
    }),
  );
  assert.equal(failed.installing, false);
  assert.equal(failed.installDisabled, false);
  assert.equal(failed.stageText, "");
  assert.equal(failed.errorText, "SHA-256 hash mismatch");
  assert.equal(formatRpgmakerInstallStage(null), "");
});
