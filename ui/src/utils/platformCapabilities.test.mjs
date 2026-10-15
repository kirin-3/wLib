// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";

const LINUX_CAPABILITIES = {
  platform: "linux",
  native_windows_launch: false,
  wine_proton: true,
  runtime_installers: true,
  wayland: true,
  rpgmaker_linux: true,
  cheat_engine_injection: true,
  launch_modes: ["auto", "native", "wine_proton", "rpgmaker_linux", "custom"],
  data_dir: "/home/wlib/.local/share/wLib",
  cache_dir: "/home/wlib/.cache/wLib",
  extension_dir: "/home/wlib/.local/share/wLib/extension",
};

const setupGlobals = () => {
  const storage = new Map();
  const mockStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.localStorage = mockStorage;
  globalThis.window = {
    localStorage: mockStorage,
    dispatchEvent() {},
    addEventListener() {},
    navigator: { userAgent: "node" },
  };
};

test("platform capabilities are cached once per desktop session", async () => {
  setupGlobals();
  let calls = 0;
  globalThis.window.pywebview = {
    api: {
      get_platform_capabilities: async () => {
        calls++;
        return LINUX_CAPABILITIES;
      },
    },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const mod = await server.ssrLoadModule("/src/utils/platformCapabilities.ts");
    const first = await mod.loadPlatformCapabilities();
    const second = await mod.loadPlatformCapabilities();

    assert.equal(first.runtime_installers, true);
    assert.equal(second, first);
    assert.equal(calls, 1);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("the browser mock result is never cached", async () => {
  setupGlobals();
  let calls = 0;
  globalThis.localStorage.setItem("wlib-mock-platform", "linux");
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const mod = await server.ssrLoadModule("/src/utils/platformCapabilities.ts");
    const first = await mod.loadPlatformCapabilities();
    const second = await mod.loadPlatformCapabilities();

    // No pywebview API: the mock answer is rebuilt per call, so both reads
    // return fresh objects and the Linux controls follow the storage value.
    assert.equal(first.runtime_installers, true);
    assert.equal(second.runtime_installers, true);
    assert.equal(calls, 0);
    assert.notEqual(second, first);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("a failed read falls back to conservative capabilities and retries", async () => {
  setupGlobals();
  let fail = true;
  let calls = 0;
  globalThis.window.pywebview = {
    api: {
      get_platform_capabilities: async () => {
        calls++;
        if (fail) throw new Error("bridge hiccup");
        return LINUX_CAPABILITIES;
      },
    },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const mod = await server.ssrLoadModule("/src/utils/platformCapabilities.ts");
    const fallback = await mod.loadPlatformCapabilities();
    assert.equal(fallback.runtime_installers, false);
    assert.equal(fallback.wine_proton, false);
    assert.deepEqual(fallback.launch_modes, ["auto"]);
    assert.equal(calls, 1);

    // No reset here on purpose: the fallback must not be cached, so the next
    // read goes back to the backend and restores the Linux runtime tools.
    fail = false;
    const recovered = await mod.loadPlatformCapabilities();
    assert.equal(recovered.runtime_installers, true);
    assert.equal(recovered.wine_proton, true);
    assert.equal(calls, 2);

    mod.resetPlatformCapabilitiesForTests();
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("concurrent readers share one in-flight request", async () => {
  setupGlobals();
  let calls = 0;
  globalThis.window.pywebview = {
    api: {
      get_platform_capabilities: async () => {
        calls++;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return LINUX_CAPABILITIES;
      },
    },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const mod = await server.ssrLoadModule("/src/utils/platformCapabilities.ts");
    const [first, second] = await Promise.all([
      mod.loadPlatformCapabilities(),
      mod.loadPlatformCapabilities(),
    ]);
    assert.equal(first, second);
    assert.equal(calls, 1);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});
