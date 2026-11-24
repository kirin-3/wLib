// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import type { CatalogThreadEntry } from "../services/api";
import { applyCatalogEntry, versionToSave, type AddGamePrefillForm } from "./addGamePrefill.ts";

const emptyForm = (): AddGamePrefillForm => ({
  title: "",
  developer: "",
  coverImage: "",
  tags: "",
  rating: "",
  engine: "",
  links: [],
  threadCompleted: undefined,
  threadOnhold: undefined,
  threadAbandoned: undefined,
  threadStatesSeenAt: "",
});

const sampleEntry: CatalogThreadEntry = {
  title: "Ambrosia",
  developer: "Shimobashira Workshop / Kagura Games",
  version: "v1.07",
  coverImage: "https://attachments.f95zone.to/ambrosia.jpg",
  tags: "2d game, rpg",
  rating: 4.5,
  engine: "RPGM",
  thread_completed: true,
  thread_onhold: false,
  thread_abandoned: false,
  thread_states_seen_at: "2026-11-01T12:00:00Z",
  links: [
    {
      source: "dlsite",
      id: "RJ256962",
      url: "https://www.dlsite.com/maniax/work/=/product_id/RJ256962.html",
    },
  ],
};

test("applyCatalogEntry fills empty fields and returns catalogVersion separately", () => {
  const result = applyCatalogEntry(emptyForm(), sampleEntry);
  assert.equal(result.title, "Ambrosia");
  assert.equal(result.developer, "Shimobashira Workshop / Kagura Games");
  assert.equal(result.coverImage, "https://attachments.f95zone.to/ambrosia.jpg");
  assert.equal(result.tags, "2d game, rpg");
  assert.equal(result.rating, "4.5/5");
  assert.equal(result.engine, "RPGM");
  assert.equal(result.links.length, 1);
  assert.equal(result.links[0]?.id, "RJ256962");
  assert.equal(result.threadCompleted, true);
  assert.equal(result.threadOnhold, false);
  assert.equal(result.threadAbandoned, false);
  assert.equal(result.threadStatesSeenAt, "2026-11-01T12:00:00Z");
  assert.equal(result.catalogVersion, "v1.07");
  assert.equal("version" in result, false);
});

test("applyCatalogEntry keeps non-empty user fields untouched", () => {
  const existingLink = {
    source: "steam",
    id: "12345",
    url: "https://store.steampowered.com/app/12345/",
  };
  const form: AddGamePrefillForm = {
    title: "My Custom Title",
    developer: "My Dev",
    coverImage: "https://mine/cover.png",
    tags: "favorite",
    rating: "4.9/5",
    engine: "Ren'Py",
    links: [existingLink],
    threadCompleted: false,
    threadOnhold: true,
    threadAbandoned: false,
    threadStatesSeenAt: "2026-10-01T00:00:00Z",
  };
  const result = applyCatalogEntry(form, sampleEntry, "Ambrosia_v1.07");
  assert.equal(result.title, "My Custom Title");
  assert.equal(result.developer, "My Dev");
  assert.equal(result.coverImage, "https://mine/cover.png");
  assert.equal(result.tags, "favorite");
  assert.equal(result.rating, "4.9/5");
  assert.equal(result.engine, "Ren'Py");
  assert.deepEqual(result.links, [existingLink]);
  assert.equal(result.threadCompleted, false);
  assert.equal(result.threadOnhold, true);
  assert.equal(result.threadAbandoned, false);
  assert.equal(result.threadStatesSeenAt, "2026-10-01T00:00:00Z");
  assert.equal(result.catalogVersion, "v1.07");
});

test("applyCatalogEntry replaces an exe-guessed title but not a typed one", () => {
  const guessedForm: AddGamePrefillForm = {
    ...emptyForm(),
    title: "Ambrosia_v1.07",
  };
  const replaced = applyCatalogEntry(guessedForm, sampleEntry, "Ambrosia_v1.07");
  assert.equal(replaced.title, "Ambrosia");

  const typedForm: AddGamePrefillForm = {
    ...emptyForm(),
    title: "My Own Name",
  };
  const kept = applyCatalogEntry(typedForm, sampleEntry, "Ambrosia_v1.07");
  assert.equal(kept.title, "My Own Name");
});

test("applyCatalogEntry skips invalid numeric ratings and handles missing entries", () => {
  assert.equal(applyCatalogEntry(emptyForm(), { ...sampleEntry, rating: 0 }).rating, "");
  assert.equal(applyCatalogEntry(emptyForm(), { ...sampleEntry, rating: 5.5 }).rating, "");
  const missing = applyCatalogEntry(emptyForm(), undefined);
  assert.equal(missing.title, "");
  assert.equal(missing.catalogVersion, "");
});

test("versionToSave uses catalogVersion only for wishlist saves with an empty version field", () => {
  // Wishlist save (no executable) with empty version -> catalog version.
  assert.equal(versionToSave("", "", "v1.07"), "v1.07");
  assert.equal(versionToSave("   ", "   ", " v1.07 "), "v1.07");
  // Wishlist save with typed version -> typed version wins.
  assert.equal(versionToSave("1.05", "", "v1.07"), "1.05");
  // Installed game with guessed or typed version -> version wins.
  assert.equal(versionToSave("1.05", "/games/ambrosia/Game.exe", "v1.07"), "1.05");
  // Installed game with empty version -> saves empty, never catalog version.
  assert.equal(versionToSave("", "/games/ambrosia/Game.exe", "v1.07"), "");
});
