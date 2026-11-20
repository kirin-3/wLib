# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[1] / "extension"

SCENARIO = r"""
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {setImmediate: flush} = require('node:timers/promises');
const vm = require('node:vm');

class Element {
    children = [];
    attributes = {};
    classList = {add() {}, remove() {}, toggle() {}};
    style = {};
    listeners = {};
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    prepend(child) { this.children.unshift(child); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    querySelector(selector) {
        for (const child of this.children) {
            if (child.className?.split(' ').includes(selector.slice(1))) return child;
            const match = child.querySelector?.(selector);
            if (match) return match;
        }
        return null;
    }
}

async function run() {
    const [extension, action] = process.argv.slice(1);
    let reachable = action !== 'retry';
    const listeners = new Set();
    const timers = [];
    const body = new Element();
    const title = {textContent: 'Example [v1.0]', querySelectorAll: () => []};
    title.cloneNode = () => title;
    const createElement = () => {
        const element = new Element();
        element.remove = () => {
            body.children = body.children.filter(child => child !== element);
        };
        return element;
    };
    const document = {
        body, createElement, createTextNode: text => ({text}), readyState: 'complete',
        getElementById: id => body.children.find(child => child.id === id),
        querySelector: selector => selector === 'h1.p-title-value' ? title : null,
        querySelectorAll: () => [],
    };
    const chrome = {
        runtime: {
            id: 'wlib',
            sendMessage(message, callback) {
                if (message.action === 'checkGameInWLib') {
                    callback(reachable
                        ? {success: true, data: {exists: action === 'open'}}
                        : {success: false, error: 'wLib is offline'});
                } else {
                    callback({success: true});
                }
            },
        },
        storage: {
            local: {get: (_key, callback) => callback({})},
            onChanged: {
                addListener: listener => listeners.add(listener),
                removeListener: listener => listeners.delete(listener),
            },
        },
    };
    const location = new URL('https://f95zone.to/threads/example.1/');
    const context = vm.createContext({
        chrome, document, window: {location}, URL, console,
        setTimeout: callback => timers.push(callback),
        requestAnimationFrame: callback => callback(),
    });
    for (const file of ['widget-placement.js', 'content.js']) {
        vm.runInContext(readFileSync(join(extension, file), 'utf8'), context);
    }
    await flush();

    const updateSide = side => {
        for (const listener of listeners) {
            listener({widgetPlacement: {newValue: {side}}}, 'local');
        }
    };
    assert.equal(listeners.size, 1);
    const [original] = body.children;
    updateSide('left');
    assert.equal(original.attributes['data-side'], 'left');

    reachable = true;
    const selector = action === 'wishlist' ? '.wlib-wishlist-btn' : '.wlib-add-btn';
    original.querySelector(selector).listeners.click();
    await flush();
    while (timers.length) timers.shift()();

    assert.ok(!body.children.includes(original));
    assert.equal(listeners.size, action === 'retry' ? 1 : 0,
        'Only the visible widget should have a storage listener');
    updateSide('right');
    assert.equal(original.attributes['data-side'], 'left');
    if (action === 'retry') {
        const [replacement] = body.children;
        updateSide('left');
        assert.equal(replacement.attributes['data-side'], 'left');
    }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("action", ["retry", "open", "add", "wishlist"])
def test_removed_widget_unsubscribes_from_placement_changes(action: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Requires Node.js")
    result = subprocess.run(
        [node, "-e", SCENARIO, str(EXTENSION), action],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
