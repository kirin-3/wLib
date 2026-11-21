# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess
from pathlib import Path

import pytest


EXTENSION = Path(__file__).resolve().parents[1] / "extension"
SCENARIO = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

async function run() {
    const [background, content, errorCode] = process.argv.slice(1);
    const threadUrl = "https://lewdcorner.com/threads/42/";
    const card = {
        dataset: { cardId: "42" },
        isConnected: true,
        classList: { add() {} },
        querySelector: selector => selector === ".wlib-library-badge" ? card.badge : null,
        append: badge => { card.badge = badge; },
    };
    let paired = false;
    let listener;
    let versionChecked;
    let pendingReply;
    const timers = [];
    const runtime = {
        onMessage: { addListener: callback => { listener = callback; } },
        getManifest: () => { versionChecked = true; return { version: "1.0.0" }; },
        getURL: () => "moz-extension://paired/",
        sendMessage(message, reply) {
            pendingReply = new Promise(resolve => {
                listener(message, {}, response => { reply(response); resolve(response); });
            });
        },
    };
    vm.runInNewContext(fs.readFileSync(background, "utf8"), {
        chrome: { runtime, storage: { local: { get: async () => ({ wlibPairingToken: "a".repeat(64) }) } } },
        AbortController, URLSearchParams, setTimeout, clearTimeout,
        console: { warn() {} },
        fetch: async () => ({
            ok: true,
            status: 200,
            json: async () => paired ? { matches: { [threadUrl]: 3 } } : {
                exists: false, error_code: errorCode, error: "Pairing failed", extension_version: "2.0.0",
            },
        }),
    });
    vm.runInNewContext(fs.readFileSync(content, "utf8"), {
        chrome: { runtime },
        location: { pathname: "/latest-updates/" },
        document: { querySelectorAll: () => [card], createElement: () => ({ dataset: {} }) },
        MutationObserver: class { observe() {} },
        setTimeout: callback => timers.push(callback),
    });

    assert.equal((await pendingReply).success, false);
    assert.equal(versionChecked, true);
    assert.equal(card.dataset.wlibChecked, undefined);
    assert.equal(timers.length, 1);

    paired = true;
    timers.shift()();
    assert.equal((await pendingReply).success, true);
    assert.equal(card.dataset.wlibChecked, "42");
    assert.equal(card.badge.textContent, "In wLib");
    console.log("Badge retry passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize(
    "code", ["pairing_required", "pairing_denied", "extension_outdated"]
)
def test_lewdcorner_cards_retry_after_pairing_error(code):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Requires Node.js")
    result = subprocess.run(
        [
            node,
            "-e",
            SCENARIO,
            str(EXTENSION / "background.js"),
            str(EXTENSION / "sites" / "lewdcorner.js"),
            code,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Badge retry passed" in result.stdout


CARD_SCENARIO = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const [content, scenario] = process.argv.slice(1);
const card = {
    dataset: { cardId: "42" },
    isConnected: true,
    classList: { add() {} },
    querySelector: selector => selector === ".wlib-library-badge" ? card.badge : null,
    append: badge => { card.badge = badge; },
};
const replies = [];
const timers = [];
let mutated;
vm.runInNewContext(fs.readFileSync(content, "utf8"), {
    chrome: { runtime: { sendMessage(_message, reply) {
        if (scenario === "reload") throw new Error("Extension context invalidated.");
        replies.push(reply);
    } } },
    location: { pathname: "/latest-updates/" },
    document: {
        querySelectorAll: () => [card],
        createElement: () => ({ dataset: {}, remove() { delete card.badge; } }),
    },
    MutationObserver: class {
        constructor(callback) { mutated = callback; }
        observe() {}
    },
    setTimeout: callback => timers.push(callback),
});

if (scenario === "reload") {
    assert.equal(timers.length, 0, "an invalidated context cannot retry");
} else {
    // Keep the first check in flight while the feed reuses the same card twice.
    for (const id of ["43", "42"]) {
        card.dataset.cardId = id;
        mutated();
        timers.shift()();
    }
    assert.equal(replies.length, 3);
    replies[2]({ success: true, matches: { "https://lewdcorner.com/threads/42/": 3 } });
    replies[0]({ success: true, matches: {} });
    assert.equal(card.badge?.textContent, "In wLib");
}
"""


@pytest.mark.parametrize("scenario", ["stale_reply", "reload"])
def test_lewdcorner_card_checks(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Requires Node.js")
    result = subprocess.run(
        [
            node,
            "-e",
            CARD_SCENARIO,
            str(EXTENSION / "sites" / "lewdcorner.js"),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
