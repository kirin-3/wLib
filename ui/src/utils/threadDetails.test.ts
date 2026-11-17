// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import { automaticTags, languageNames, linkLabel, parseLinkLines } from "./threadDetails.ts";

test("language names come from the free text", () => {
  const cases: [string, string[]][] = [
    ["English", ["English"]],
    ["English (Sonnet 5.5 & Opus 5.5)", ["English"]],
    ["English, Japanese", ["English", "Japanese"]],
    ["English, Japanase", ["English", "Japanase"]],
    ["Japanese (Original) / English (MTL)", ["Japanese", "English"]],
    ["english + chinese", ["English", "Chinese"]],
    ["English MTL", ["English"]],
    ["Spanish and Portuguese", ["Spanish", "Portuguese"]],
    ["Other", []],
    ["", []],
  ];
  for (const [text, names] of cases) assert.deepEqual(languageNames(text), names, text);
});

test("link labels name the known sites and fall back to the host", () => {
  const cases: [string, string][] = [
    ["https://www.patreon.com/cw/dev", "Patreon"],
    ["https://subscribestar.adult/dev", "SubscribeStar"],
    ["https://dev.itch.io/game", "itch.io"],
    ["https://ko-fi.com/dev", "Ko-fi"],
    ["https://store.steampowered.com/app/1/", "Steam"],
    ["https://discord.gg/abc", "Discord"],
    ["https://www.example.org/x", "example.org"],
    ["not a url", "not a url"],
  ];
  for (const [url, label] of cases) assert.equal(linkLabel(url), label, url);
});

test("link lines keep http(s) URLs once, in order", () => {
  assert.deepEqual(parseLinkLines(" https://a.test/1 \n\nfoo\nhttps://a.test/1\nhttp://b.test"), [
    "https://a.test/1",
    "http://b.test",
  ]);
});

test("automatic tags come from the developer and the language", () => {
  assert.deepEqual(automaticTags({ developer: " DrPinkCake ", language: "English (MTL), Japanese" }), [
    "Developer: DrPinkCake",
    "Language: English",
    "Language: Japanese",
  ]);
  assert.deepEqual(automaticTags({ developer: "", language: "" }), []);
});
