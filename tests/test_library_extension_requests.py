# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"
VIEW = UI / "src/views/LibraryView.vue"

# Run the actual watcher with Vue refs and a memory router. Only the library
# refresh, modal close, and next tick can be held until another route arrives.
SCENARIO = r"""
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { nextTick: flushVue, ref, watch: vueWatch } = require("vue");
const { createRouter, createMemoryHistory } = require("vue-router");

async function run() {
  const [view, pause] = process.argv.slice(1);
  const source = readFileSync(view, "utf8");
  const watcher = source.match(/\/\/ Handle incoming extension import\s+(watch\([\s\S]*?\n\);)/);
  assert.ok(watcher, "Missing extension request watcher");

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: {} }],
  });
  const route = { get query() { return router.currentRoute.value.query; } };
  const replacements = [];
  const replace = router.replace;
  router.replace = (location) => {
    const result = replace(location);
    replacements.push(result);
    return result;
  };
  let pending;
  const watch = (source, callback, options) => vueWatch(source, (query) => {
    pending = callback(query);
    return pending;
  }, options);

  const deferred = () => {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
  };
  const first = deferred();
  const latest = deferred();
  const paused = deferred();
  const nextTick = () => {
    paused.resolve();
    return first.promise;
  };
  const games = ref([{ id: 1 }]);
  const gamesStore = { refresh: () => pause === "refresh" ? first.promise : Promise.resolve() };
  const loadGames = () => route.query.title === "B" ? latest.promise : Promise.resolve();
  const showAddModal = ref(false);
  const showDetailModal = ref(pause !== "refresh");
  const selectedGame = ref({ id: 2 });
  const detailModal = ref({
    async requestClose() {
      if (route.query.title !== "B" && pause.endsWith("-close")) {
        paused.resolve();
        if (!(await first.promise)) return false;
      }
      showDetailModal.value = false;
      return true;
    },
  });
  const opened = [];
  const openDetail = (game) => opened.push(game.id);
  const readQueryValue = (value) => value ?? "";
  const extensionGameId = (value) => Number(value);
  const normalizeLcThreadUrl = () => "";

  eval(watcher[1]);
  const importQuery = (title, id) => ({
    action: "import", f95url: `https://f95zone.to/threads/${id}/`, title,
  });
  const firstQuery = pause === "import-close"
    ? importQuery("A", 1)
    : { action: "open", gameId: "1" };
  await router.push({ path: "/", query: firstQuery });
  await flushVue();
  const earlierRequest = pending;
  assert.ok(earlierRequest);
  if (pause !== "refresh") await paused.promise;

  await router.push({ path: "/", query: importQuery("B", 2) });
  await flushVue();
  const newerRequest = pending;
  assert.notEqual(earlierRequest, newerRequest);
  first.resolve(pause !== "import-close");
  await earlierRequest;
  await Promise.all(replacements);
  latest.resolve();
  await newerRequest;
  assert.equal(showAddModal.value, true, "The latest import should open the Add Game dialog");
  assert.equal(route.query.title, "B", "The import must retain its metadata");
  assert.deepEqual(opened, [], "The superseded game should never open");
}

run().then(
  () => console.log("Extension request assertions passed"),
  (error) => {
    console.error(error);
    process.exitCode = 1;
  },
);
"""


@pytest.mark.parametrize("pause", ["refresh", "open-close", "open-tick", "import-close"])
def test_new_extension_import_supersedes_pending_request(pause: str) -> None:
    node = shutil.which("node")
    if node is None or not (UI / "node_modules/vue-router/package.json").is_file():
        pytest.skip("Requires Node.js and npm ci in ui/")
    result = subprocess.run(
        [node, "-e", SCENARIO, str(VIEW), pause],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Extension request assertions passed" in result.stdout
