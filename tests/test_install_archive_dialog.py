# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.downloads import manager as download_manager

UI = Path(__file__).resolve().parents[1] / "ui"
DIALOG = UI / "src/components/modals/InstallArchiveModal.vue"

CHECK_RELEASES = """
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { compileScript, parse } = require("@vue/compiler-sfc");
const ts = require("typescript");
const { createRenderer, h, nextTick, ref } = require("vue");

const first = "/downloads/Game-1.zip";
const second = "/downloads/Game-2.zip";
const openArchives = new Set();
const api = {
  getSettings: async () => ({ library_folder: "/library" }),
  listLibraryFolders: async () => [{ path: "/library", available: true }],
  inspectArchive: async (path) => {
    openArchives.add(path);
    return { success: true, downloaded_by_wlib: true, title: "Game" };
  },
  releaseDownloadArchive: async (path) => {
    openArchives.delete(path);
    return { success: true };
  },
};

function load(source, imports = {}) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  new Function("require", "exports", outputText)(
    (name) => Object.hasOwn(imports, name) ? imports[name] : require(name),
    exports,
  );
  return exports;
}

const filename = process.argv[1];
const { descriptor } = parse(fs.readFileSync(filename, "utf8"), { filename });
const component = load(compileScript(descriptor, { id: "archive-dialog" }).content, {
  "../../services/api": { api },
  "../../utils/installArchive": load(fs.readFileSync("src/utils/installArchive.ts", "utf8")),
  "../../utils/toast": {},
  "../../utils/modalKeyboard": { useModalKeyboard() {} },
  "../../utils/libraryGames": {},
  "../PathStatus.vue": {},
}).default;
component.render = () => null;

const renderer = createRenderer({
  createComment: () => ({}),
  parentNode: () => null,
  nextSibling: () => null,
  insert() {},
  remove() {},
});
const isOpen = ref(false);
const dialog = ref();
const app = renderer.createApp({
  render: () => h(component, {
    ref: dialog,
    modelValue: isOpen.value,
    "onUpdate:modelValue": (value) => { isOpen.value = value; },
  }),
});

async function check() {
  app.mount({});
  isOpen.value = true;
  await nextTick();
  const form = dialog.value.$.setupState;
  form.archive = first;
  await form.inspect();
  assert.deepEqual([...openArchives], [first]);
  await form.inspect();
  assert.deepEqual([...openArchives], [first], "Re-inspection keeps the current archive open");

  if (process.argv[2] === "replacement") {
    form.archive = second;
    await form.inspect();
    assert.deepEqual([...openArchives], [second], "Replacement releases the original archive");
  } else {
    form.archive = "";
  }
  form.close();
  await nextTick();
  assert.deepEqual([...openArchives], [], "Closing releases the archive held by the dialog");
  app.unmount();
}

check().catch((error) => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("scenario", ["replacement", "uncommitted-edit"])
def test_archive_dialog_releases_staged_download(scenario: str) -> None:
    node = shutil.which("node")
    if node is None or not (UI / "node_modules/@vue/compiler-sfc").is_dir():
        pytest.skip("Node and the frontend development dependencies are required")

    dialog = os.environ.get("WLIB_TEST_INSTALL_ARCHIVE_DIALOG", str(DIALOG))
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", CHECK_RELEASES, dialog, scenario],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


CHECK_DOWNLOAD_VERSION = """
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { compileScript, parse } = require("@vue/compiler-sfc");
const ts = require("typescript");
const { createRenderer, h, nextTick, ref } = require("vue");

const api = {
  getSettings: async () => ({ library_folder: "/library" }),
  listLibraryFolders: async () => [{ path: "/library", available: true }],
  inspectArchive: async () => ({ success: true, title: "Game", version: "2.0", library_folder: "/library" }),
  releaseDownloadArchive: async () => ({ success: true }),
  previewInstallDestination: async () => ({ destination: "/library/Game", engine_pending: false }),
};

function load(source, imports = {}) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  new Function("require", "exports", outputText)(
    (name) => Object.hasOwn(imports, name) ? imports[name] : require(name),
    exports,
  );
  return exports;
}

const filename = process.argv[1];
const { descriptor } = parse(fs.readFileSync(filename, "utf8"), { filename });
const component = load(compileScript(descriptor, { id: "archive-dialog" }).content, {
  "../../services/api": { api },
  "../../utils/installArchive": load(fs.readFileSync("src/utils/installArchive.ts", "utf8")),
  "../../utils/toast": { notifyError() {} },
  "../../utils/modalKeyboard": { useModalKeyboard() {} },
  "../../utils/libraryGames": { isWishlisted: () => false, defaultTargetLabel: () => "" },
  "../PathStatus.vue": {},
}).default;
component.render = () => null;

const renderer = createRenderer({
  createComment: () => ({}),
  parentNode: () => null,
  nextSibling: () => null,
  insert() {},
  remove() {},
});
// An update check found 3.0 after the 2.0 download was queued.
const game = { id: 7, title: "Game", developer: "Dev", engine: "Ren'Py", f95_url: "", version: "1.0", latest_version: "3.0", exe_path: "/g/game.sh" };
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); };

async function versionFor(downloadVersion) {
  const isOpen = ref(false);
  const dialog = ref();
  const app = renderer.createApp({
    render: () => h(component, {
      ref: dialog,
      modelValue: isOpen.value,
      "onUpdate:modelValue": (value) => { isOpen.value = value; },
      game,
      preselectedArchive: "/downloads/Game-2.0.zip",
      downloadVersion,
    }),
  });
  app.mount({});
  isOpen.value = true;
  await nextTick();
  await settle();
  const form = dialog.value.$.setupState;
  assert.equal(form.phase, "ready");
  const version = form.version;
  isOpen.value = false;
  await nextTick();
  app.unmount();
  return version;
}

async function check() {
  assert.equal(await versionFor("2.0"), "2.0", "A queued download installs under the version it fetched");
  assert.equal(await versionFor(undefined), "3.0", "A manual install keeps the latest version");
}

check().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def test_archive_dialog_keeps_queued_download_version() -> None:
    node = shutil.which("node")
    if node is None or not (UI / "node_modules/@vue/compiler-sfc").is_dir():
        pytest.skip("Node and the frontend development dependencies are required")

    dialog = os.environ.get("WLIB_TEST_INSTALL_ARCHIVE_DIALOG", str(DIALOG))
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", CHECK_DOWNLOAD_VERSION, dialog],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_release_typed_home_path_allows_deleting_staged_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(download_manager, "staging_dir", lambda: tmp_path)
    manager = download_manager.DownloadManager()
    archive = tmp_path / "Game.zip"
    archive.touch()

    # Inspection expands the home directory before marking the archive open.
    manager.mark_open(str(archive))
    assert not manager.delete_archive(str(archive))
    manager.release("~/Game.zip")
    assert manager.delete_archive(str(archive))
    assert not archive.exists()
