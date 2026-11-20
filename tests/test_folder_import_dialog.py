# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"
DIALOG = UI / "src/components/modals/ImportFolderModal.vue"

CHECK_PATH_EDIT = """
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { compileScript, parse } = require("@vue/compiler-sfc");
const ts = require("typescript");
const { createRenderer, h, ref } = require("vue");

const first = "/games/A";
const second = "/games/B";
const game = (path) => ({ title: path, exe_path: `${path}/Game.exe`, in_library: false });
let finishFirstScan;
const firstScan = new Promise((resolve) => { finishFirstScan = resolve; });
const imported = [];
const api = {
  scanGamesFolder: (path) => path === first ? firstScan : { success: true, games: [game(path)] },
  importGames: async (games) => {
    imported.push(...games.map(({ exe_path }) => exe_path));
    return { added: games.length, errors: [] };
  },
};

const filename = process.argv[1];
const { descriptor } = parse(fs.readFileSync(filename, "utf8"), { filename });
const { outputText } = ts.transpileModule(compileScript(descriptor, { id: "folder-import" }).content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
});
const imports = {
  "../../services/api": { api },
  "../../utils/toast": { notify() {}, notifyError: assert.fail },
  "../../utils/modalKeyboard": { useModalKeyboard() {} },
  "../PathStatus.vue": {},
};
const compiled = {};
new Function("require", "exports", outputText)(
  (name) => Object.hasOwn(imports, name) ? imports[name] : require(name),
  compiled,
);
const component = compiled.default;
component.render = () => null;

const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {} });
const dialog = ref();
const app = renderer.createApp({ render: () => h(component, { ref: dialog, modelValue: true }) });

async function check() {
  app.mount({});
  const form = dialog.value.$.setupState;
  form.folder = first;
  const scan = form.scan();
  const complete = () => finishFirstScan({ success: true, games: [game(first)] });

  if (process.argv[2] === "completed") {
    complete();
    await scan;
    assert.equal(form.selectedGames.length, 1, "The first scan selected a game");
    form.folder = second;
  } else {
    form.folder = second;
    complete();
    await scan;
  }

  await form.importSelected();
  assert.deepEqual(imported, [], "A scan for folder A cannot import games under folder B");
  assert.deepEqual(form.games, [], "Old scan results should be gone");
  assert.equal(form.scanning, false, "The next scan must be available");

  await form.scan();
  await form.importSelected();
  assert.deepEqual(imported, [game(second).exe_path], "Import uses the new folder's scan");
  app.unmount();
}

check().catch((error) => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("scenario", ["completed", "pending"])
def test_editing_folder_invalidates_scan(scenario: str) -> None:
    node = shutil.which("node")
    if node is None or not (UI / "node_modules/@vue/compiler-sfc").is_dir():
        pytest.skip("Node and the frontend development dependencies are required")

    dialog = os.environ.get("WLIB_TEST_IMPORT_FOLDER_DIALOG", str(DIALOG))
    result = subprocess.run(
        [node, "--input-type=commonjs", "-e", CHECK_PATH_EDIT, dialog, scenario],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
