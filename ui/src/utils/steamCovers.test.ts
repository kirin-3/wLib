// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { convertSteamCovers } from "./steamCovers.ts";

test("converts queued covers and reports failures with empty data", async () => {
  const queue = [
    { token: "a.png", type: "image/avif", data: "AAAA" },
    { token: "b.png", type: "image/avif", data: "BAD" },
  ];
  const saved: [string, string][] = [];
  const api = {
    getSteamCoverConversions: async () => ({ success: true, covers: queue.splice(0, 1) }),
    saveSteamCoverPng: async (token: string, data: string) => { saved.push([token, data]); return { success: true }; },
  };
  const encode = async (data: string) => { if (data === "BAD") throw new Error("decode"); return "PNG"; };
  assert.equal(await convertSteamCovers(api, encode), 1);
  assert.deepEqual(saved, [["a.png", "PNG"], ["b.png", ""]]);
});

test("stops when the backend has nothing queued or is unsupported", async () => {
  let calls = 0;
  const api = {
    getSteamCoverConversions: async () => { calls++; return { success: false }; },
    saveSteamCoverPng: async () => ({ success: true }),
  };
  assert.equal(await convertSteamCovers(api, async () => "PNG"), 0);
  assert.equal(calls, 1);
});
