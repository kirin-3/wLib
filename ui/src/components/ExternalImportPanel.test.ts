// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import vue from "@vitejs/plugin-vue";
import { createServer, type Plugin } from "vite";
import { createRenderer, defineComponent, h, nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

interface NodeStub {
  type: string;
  tagName: string;
  props: Record<string, unknown>;
  children: NodeStub[];
  parent: NodeStub | null;
  value: string;
  text?: string;
  listeners: Record<string, Array<(e: unknown) => void>>;
  addEventListener: (name: string, fn: (e: unknown) => void) => void;
  removeEventListener: (name: string, fn: (e: unknown) => void) => void;
  dispatch: (name: string, event: unknown) => void;
  getRootNode: () => { activeElement: null };
}

function makeNoopRenderer() {
  const createNode = (type: string): NodeStub => {
    const node: NodeStub = {
      type,
      tagName: type.toUpperCase(),
      props: {},
      children: [],
      parent: null,
      value: "",
      listeners: {},
      addEventListener(name, fn) {
        (node.listeners[name] ||= []).push(fn);
      },
      removeEventListener(name, fn) {
        node.listeners[name] = (node.listeners[name] || []).filter((f) => f !== fn);
      },
      dispatch(name, event) {
        for (const fn of node.listeners[name] || []) fn(event);
      },
      getRootNode() {
        return { activeElement: null };
      },
    };
    return node;
  };
  return createRenderer<NodeStub, NodeStub>({
    patchProp(el, key, _prev, next) {
      if (key === "value") el.value = String(next ?? "");
      if (key.startsWith("on") && typeof next === "function") {
        const eventName = key.slice(2).toLowerCase();
        el.listeners[eventName] = [next as (e: unknown) => void];
      }
      if (next == null) delete el.props[key];
      else el.props[key] = next;
    },
    insert(child, parent, anchor) {
      if (child.parent) {
        const oldIdx = child.parent.children.indexOf(child);
        if (oldIdx >= 0) child.parent.children.splice(oldIdx, 1);
      }
      child.parent = parent;
      const idx = anchor ? parent.children.indexOf(anchor) : -1;
      if (idx >= 0) parent.children.splice(idx, 0, child);
      else parent.children.push(child);
    },
    remove(child) {
      if (!child.parent) return;
      const idx = child.parent.children.indexOf(child);
      if (idx >= 0) child.parent.children.splice(idx, 1);
      child.parent = null;
    },
    createElement(type) {
      return createNode(type);
    },
    createText(text) {
      return { ...createNode("#text"), text };
    },
    createComment(text) {
      return { ...createNode("#comment"), text };
    },
    setText(node, text) {
      node.text = text;
    },
    setElementText(el, text) {
      el.children = [{ ...createNode("#text"), parent: el, text }];
    },
    parentNode(node) {
      return node.parent;
    },
    nextSibling(node) {
      if (!node.parent) return null;
      const idx = node.parent.children.indexOf(node);
      return idx >= 0 ? (node.parent.children[idx + 1] ?? null) : null;
    },
  });
}

function walkNodes(node: NodeStub, out: NodeStub[] = []): NodeStub[] {
  out.push(node);
  for (const child of node.children) walkNodes(child, out);
  return out;
}

function textOf(node: NodeStub): string {
  if (node.text) return node.text;
  return node.children.map(textOf).join("");
}

function makeClientVuePlugin(): Plugin {
  const vuePlugin = vue() as Plugin;
  const origTransform = vuePlugin.transform;
  return {
    ...vuePlugin,
    transform:
      origTransform && typeof origTransform === "object" && "handler" in origTransform
        ? {
            ...origTransform,
            handler(code, id, options) {
              return origTransform.handler.call(this, code, id, { ...options, ssr: false });
            },
          }
        : typeof origTransform === "function"
          ? function (code, id, options) {
              return origTransform.call(this, code, id, { ...options, ssr: false });
            }
          : origTransform,
  };
}

test("ExternalImportPanel emits busy true/false and imported result, and hides page heading in embedded mode", async () => {
  let resolveImport: ((value: unknown) => void) | undefined;
  const importResult = {
    success: true,
    created: 3,
    updated: 1,
    skipped: 0,
    metadata_check: "none",
  };
  globalThis.Document = class {} as unknown as typeof Document;
  globalThis.ShadowRoot = class {} as unknown as typeof ShadowRoot;
  globalThis.window = {
    pywebview: {
      api: {
        detect_external_library: async () => ({
          success: true,
          path: "/mock/f95checker.db",
          checked: ["/mock/f95checker.db"],
        }),
        check_path: async () => ({ ok: true, reason: "" }),
        inspect_external_library: async () => ({
          success: true,
          available_sections: ["user_state", "executable_paths"],
          counts: {
            total_games: 4,
            matched_games: 1,
            new_games: 3,
            ambiguous_games: 0,
            skipped_no_identity: 0,
            dropped_paths: 0,
            covers_to_copy: 0,
          },
          warnings: [],
        }),
        import_external_library: () =>
          new Promise((resolve) => {
            resolveImport = resolve;
          }),
      },
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  } as unknown as Window & typeof globalThis;

  const server = await createServer({
    configFile: false,
    optimizeDeps: { noDiscovery: true },
    plugins: [makeClientVuePlugin()],
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });

  try {
    const ExternalImportPanel = (
      await server.ssrLoadModule("/src/components/ExternalImportPanel.vue")
    ).default;

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: defineComponent({ render: () => h("div") }) },
        { path: "/updates", component: defineComponent({ render: () => h("div") }) },
      ],
    });
    await router.push("/");
    await router.isReady();

    // 1. Page variant renders the heading.
    const pageRenderer = makeNoopRenderer();
    const pageRoot = pageRenderer.createApp(
      defineComponent({
        render: () => h(ExternalImportPanel, { variant: "page" }),
      }),
    );
    pageRoot.use(router);
    const pageContainer = {
      type: "div",
      tagName: "DIV",
      props: {},
      children: [],
      parent: null,
      value: "",
      listeners: {},
      addEventListener() {},
      removeEventListener() {},
      dispatch() {},
      getRootNode: () => ({ activeElement: null }),
    };
    pageRoot.mount(pageContainer);
    await nextTick();
    assert.ok(
      walkNodes(pageContainer).some((node) => node.type === "h3" && textOf(node).includes("From another app")),
      "page variant should include the page-level heading",
    );
    assert.ok(
      walkNodes(pageContainer).some((node) => node.type === "button" && textOf(node).includes("Import Selected")),
      "page variant keeps its own Import Selected button",
    );
    pageRoot.unmount();

    // 2. Embedded variant hides the page heading, and emits busy true -> false + imported.
    const busyEvents: boolean[] = [];
    const importedEvents: unknown[] = [];
    const pendingEvents: Array<{ count: number; run: () => Promise<boolean> } | null> = [];
    const embeddedRenderer = makeNoopRenderer();
    const embeddedApp = embeddedRenderer.createApp(
      defineComponent({
        render: () =>
          h(ExternalImportPanel, {
            variant: "embedded",
            onBusy: (value: boolean) => busyEvents.push(value),
            onImported: (res: unknown) => importedEvents.push(res),
            onPendingImport: (req: { count: number; run: () => Promise<boolean> } | null) =>
              pendingEvents.push(req),
          }),
      }),
    );
    embeddedApp.use(router);
    const embeddedContainer: NodeStub = {
      type: "div",
      tagName: "DIV",
      props: {},
      children: [],
      parent: null,
      value: "",
      listeners: {},
      addEventListener() {},
      removeEventListener() {},
      dispatch() {},
      getRootNode: () => ({ activeElement: null }),
    };
    embeddedApp.mount(embeddedContainer);

    // Wait for auto-detection on mount.
    for (let i = 0; i < 6; i++) {
      await new Promise((r) => setTimeout(r, 10));
      await nextTick();
    }

    assert.equal(
      walkNodes(embeddedContainer).some((node) => node.type === "h3"),
      false,
      "embedded variant must hide the page-level heading",
    );

    const findButton = (label: string) =>
      walkNodes(embeddedContainer).find(
        (node) => node.type === "button" && textOf(node).includes(label),
      );

    const inspectBtn = findButton("Inspect Library");
    assert.ok(inspectBtn, "Inspect Library button should exist");
    inspectBtn.dispatch("click", {});
    for (let i = 0; i < 6; i++) {
      await new Promise((r) => setTimeout(r, 10));
      await nextTick();
    }

    assert.equal(
      findButton("Import Selected"),
      undefined,
      "embedded variant must hide its own Import Selected button",
    );
    const pending = pendingEvents[pendingEvents.length - 1];
    assert.ok(pending, "embedded variant should emit a pending import after inspect");
    assert.equal(pending.count, 4, "count is new plus matched games");
    const runResult = pending.run();
    await nextTick();

    assert.deepEqual(busyEvents, [true], "busy should emit true while import is running");
    assert.equal(importedEvents.length, 0);

    assert.ok(resolveImport, "import_external_library should have been called");
    resolveImport(importResult);
    for (let i = 0; i < 6; i++) {
      await new Promise((r) => setTimeout(r, 10));
      await nextTick();
    }

    assert.deepEqual(busyEvents, [true, false], "busy should emit false after import finishes");
    assert.deepEqual(importedEvents, [importResult], "imported should emit the backend result");
    assert.equal(await runResult, true, "run resolves true on success");
    assert.equal(pendingEvents[pendingEvents.length - 1], null, "pending import clears after success");
    embeddedApp.unmount();
  } finally {
    await server.close();
  }
});
