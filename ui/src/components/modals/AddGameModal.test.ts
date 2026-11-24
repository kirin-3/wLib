// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import vue from "@vitejs/plugin-vue";
import { createServer, type Plugin } from "vite";
import { createRenderer, defineComponent, h, nextTick, ref } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

interface NodeStub {
  type: string;
  tagName: string;
  props: Record<string, unknown>;
  children: NodeStub[];
  parent: NodeStub | null;
  value: string;
  text?: string;
  options: NodeStub[];
  selectedIndex: number;
  listeners: Record<string, Array<(e: unknown) => void>>;
  addEventListener: (name: string, fn: (e: unknown) => void) => void;
  removeEventListener: (name: string, fn: (e: unknown) => void) => void;
  dispatch: (name: string, event: unknown) => void;
  focus: () => void;
  querySelectorAll: () => NodeStub[];
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
      options: [],
      selectedIndex: -1,
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
      focus() {},
      querySelectorAll() {
        return [];
      },
    };
    return node;
  };
  return createRenderer<NodeStub, NodeStub>({
    patchProp(el, key, _prev, next) {
      if (key === "value") el.value = String(next ?? "");
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

test("AddGameModal carries hidden original_title from query to addGame and clears it on reset", async () => {
  const addGameCalls: unknown[][] = [];
  globalThis.window = {
    pywebview: {
      api: {
        get_settings: async () => ({ lc_enabled: false }),
        get_platform_capabilities: async () => ({
          os: "linux",
          rpgmaker_linux: false,
          sandbox: "bwrap",
          steam: false,
          locale_emulator: false,
          gamemode: false,
          mangohud: false,
          gamescope: false,
        }),
        add_game: async (...args: unknown[]) => {
          addGameCalls.push(args);
          return { success: true, id: addGameCalls.length };
        },
      },
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  } as unknown as Window & typeof globalThis;

  const vuePlugin = vue() as Plugin;
  const origTransform = vuePlugin.transform;
  const clientVuePlugin: Plugin = {
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
  const server = await createServer({
    configFile: false,
    optimizeDeps: { noDiscovery: true },
    plugins: [clientVuePlugin],
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  try {
    const { extensionAddQuery } = await server.ssrLoadModule("/src/utils/storeLinks.ts");
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    const AddGameModal = (await server.ssrLoadModule("/src/components/modals/AddGameModal.vue")).default;

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    const dlsiteLink = {
      source: "dlsite" as const,
      id: "RJ01609862",
      url: "https://www.dlsite.com/maniax/work/=/product_id/RJ01609862.html",
    };
    await router.push({
      path: "/",
      query: extensionAddQuery({
        title: "Summer Witches!",
        original_title: "なつまじょ！ -二人の魔女と真夏の田舎生活-",
        developer: "Yumiyanomori",
        tags: ["Married Person", "Married Woman"],
        links: [dlsiteLink],
      }),
    });
    await router.isReady();

    const isOpen = ref(true);
    const savedPayloads: Record<string, unknown>[] = [];
    const Host = defineComponent({
      setup() {
        return () =>
          h(AddGameModal, {
            modelValue: isOpen.value,
            "onUpdate:modelValue": (value: boolean) => {
              isOpen.value = value;
            },
            onSave: async (payload: Record<string, unknown>) => {
              savedPayloads.push(payload);
              await api.addGame(
                String(payload.title || ""),
                String(payload.exe_path || ""),
                String(payload.f95_url || ""),
                String(payload.version || ""),
                String(payload.cover_image || ""),
                String(payload.tags || ""),
                String(payload.rating || ""),
                String(payload.developer || ""),
                String(payload.engine || ""),
                false,
                false,
                false,
                "",
                "",
                (payload.launch_mode as "auto") || "auto",
                String(payload.command_line_args || ""),
                (payload.links as typeof dlsiteLink[]) || [],
                String(payload.lc_url || ""),
                "",
                String(payload.lc_dismissed || ""),
                payload.thread_completed as boolean | undefined,
                (payload.screenshots as string[]) || [],
                payload.thread_onhold as boolean | undefined,
                payload.thread_abandoned as boolean | undefined,
                String(payload.thread_states_seen_at || ""),
                String(payload.original_title || ""),
              );
            },
          });
      },
    });

    const container: NodeStub = {
      type: "root",
      tagName: "ROOT",
      props: {},
      children: [],
      parent: null,
      value: "",
      options: [],
      selectedIndex: -1,
      listeners: {},
      addEventListener() {},
      removeEventListener() {},
      dispatch() {},
      focus() {},
      querySelectorAll() {
        return [];
      },
    };
    const app = makeNoopRenderer().createApp(Host);
    app.use(router);
    app.mount(container);
    await nextTick();
    await new Promise((resolve) => setTimeout(resolve, 10));

    // original_title is hidden: no input shows or edits it.
    const nodes = walkNodes(container);
    const inputs = nodes.filter((node) => node.type === "input");
    assert.ok(inputs.some((input) => input.value === "Summer Witches!"));
    assert.ok(
      !inputs.some((input) => input.value.includes("なつまじょ")),
      "original_title should not be rendered in a visible input",
    );

    // Click "Save to library" and verify original_title reaches add_game.
    const saveButton = nodes.find(
      (node) => node.type === "button" && textOf(node).includes("Save to library"),
    );
    assert.ok(saveButton && typeof saveButton.props.onClick === "function");
    (saveButton.props.onClick as () => void)();
    await nextTick();
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.equal(savedPayloads.length, 1);
    assert.equal(savedPayloads[0]!.title, "Summer Witches!");
    assert.equal(savedPayloads[0]!.original_title, "なつまじょ！ -二人の魔女と真夏の田舎生活-");
    assert.equal(addGameCalls.length, 1);
    assert.equal(addGameCalls[0]![0], "Summer Witches!");
    assert.equal(addGameCalls[0]![25], "なつまじょ！ -二人の魔女と真夏の田舎生活-");

    // Closing resets original_title; reopening for a manual add emits "".
    isOpen.value = false;
    await nextTick();
    await router.replace({ path: "/", query: {} });
    isOpen.value = true;
    await nextTick();

    const reopenedNodes = walkNodes(container);
    const titleInput = reopenedNodes.find(
      (node) => node.type === "input" && node.props.placeholder === "e.g. My Awesome RPG",
    );
    const exeInput = reopenedNodes.find(
      (node) => node.type === "input" && node.props.placeholder === "/path/to/game.exe",
    );
    assert.ok(titleInput);
    assert.ok(exeInput);
    const setInputValue = (input: NodeStub, value: string) => {
      input.value = value;
      input.dispatch("input", { target: input });
      const handler = input.props["onUpdate:modelValue"];
      if (typeof handler === "function") (handler as (v: string) => void)(value);
    };
    setInputValue(titleInput, "Manual Game");
    setInputValue(exeInput, "/games/manual.sh");
    await nextTick();

    const reopenedSave = walkNodes(container).find(
      (node) => node.type === "button" && textOf(node).includes("Save to library"),
    );
    assert.ok(reopenedSave && typeof reopenedSave.props.onClick === "function");
    (reopenedSave.props.onClick as () => void)();
    await nextTick();
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.equal(savedPayloads.length, 2);
    assert.equal(savedPayloads[1]!.original_title, "");
    assert.equal(addGameCalls.length, 2);
    assert.equal(addGameCalls[1]![25], "");

    app.unmount();
  } finally {
    await server.close();
    delete (globalThis as { window?: unknown }).window;
  }
});
