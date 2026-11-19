// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { defaultDownloadSite, downloadSites, latestSiteLabel, lcActive, lcPlusCheckedAt, lcPlusCheckReason, lcPlusDetectedText, normalizeLcThreadUrl, showLcFields, updateCheckUrl, type LewdCornerMode } from "./lewdcorner.ts";

const url = "https://lewdcorner.com/threads/123/";

test("normalizes LC thread URL variants and rejects other pages", () => {
  for (const input of [url, "www.lewdcorner.com/threads/slug.123/post-4#post-4", "http://lewdcorner.com/threads/slug.123/page-2?b=1"]) {
    assert.equal(normalizeLcThreadUrl(input), url);
  }
  for (const input of ["https://lewdcorner.com/forums/games.6/", "https://other.com/threads/123/", "/threads/123/", "https://lewdcorner.com.evil/threads/123/"]) {
    assert.equal(normalizeLcThreadUrl(input), "");
  }
});

test("activation requires an LC thread and respects the game override", () => {
  const f95 = "https://f95zone.to/threads/456/";
  const cases: [LewdCornerMode, boolean, string, boolean][] = [
    ["", false, f95, false], ["", true, f95, true], ["", false, "", true],
    ["on", false, f95, true], ["off", true, "", false],
  ];
  for (const [mode, enabled, thread, expected] of cases) {
    const game = { lc_url: url, lc_mode: mode, f95_url: thread };
    assert.equal(lcActive(game, enabled), expected);
    assert.equal(lcActive({ ...game, lc_url: "" }, enabled), false);
  }
});


test("checks an active LC-only game and prefers the F95 URL for a linked game", () => {
  const f95 = "https://f95zone.to/threads/456/";
  assert.equal(updateCheckUrl({ lc_url: url }), url);
  assert.equal(updateCheckUrl({ lc_url: url, lc_mode: "off" }), "");
  assert.equal(updateCheckUrl({ lc_url: url, f95_url: f95 }), f95);
});

test("labels a sole winning site only for games with both sites active", () => {
  const game = { lc_url: url, f95_url: "https://f95zone.to/threads/456/" };
  assert.equal(latestSiteLabel({ ...game, latest_site: "lc" }, true), "on LewdCorner");
  assert.equal(latestSiteLabel({ ...game, latest_site: "f95", lc_mode: "on" }, false), "on F95Zone");
  assert.equal(latestSiteLabel({ ...game, latest_site: "both" }, true), "");
  assert.equal(latestSiteLabel({ ...game, latest_site: "lc", lc_mode: "off" }, true), "");
  assert.equal(latestSiteLabel({ ...game, latest_site: "lc" }, false), "");
  assert.equal(latestSiteLabel({ lc_url: url, latest_site: "lc" }, true), "");
  assert.equal(latestSiteLabel({ f95_url: game.f95_url, latest_site: "f95" }, true), "");
});

test("download sites respect LC settings and default to the latest eligible site", () => {
  const game = {
    f95_url: "https://f95zone.to/threads/1/", lc_url: url, lc_mode: "" as const,
    f95_latest_version: "v1", lc_latest_version: "v2",
  };
  const settings = { lc_enabled: true, lc_plus: false };
  for (const latest_site of ["lc", "f95", "both", ""] as const) {
    const record = { ...game, latest_site };
    const sites = downloadSites(record, settings);
    assert.equal(defaultDownloadSite(record, sites), latest_site === "lc" ? "lc" : "f95");
    assert.deepEqual(sites.map(({version, older}) => [version, older]), [
      ["v1", latest_site === "lc"], ["v2", latest_site === "f95"],
    ]);
  }
  assert.deepEqual(
    downloadSites({...game, lc_latest_version: "", latest_site: "f95"}, settings).map(item => item.older),
    [false, false],
  );
  assert.deepEqual(downloadSites(game, {lc_enabled: false}).map(item => item.site), ["f95"]);
  assert.deepEqual(downloadSites({...game, lc_mode: "off"}, settings).map(item => item.site), ["f95"]);
  assert.deepEqual(downloadSites(game, settings, "Premium").map(item => item.site), ["f95"]);
  assert.equal(downloadSites(game, {...settings, lc_plus: true}, "Premium").length, 2);
  const lcOnly = {...game, f95_url: "", latest_site: "f95" as const};
  assert.equal(defaultDownloadSite(lcOnly, downloadSites(lcOnly, {})), "lc");
  assert.equal(defaultDownloadSite(lcOnly, downloadSites(lcOnly, settings, "premium")), "");
  assert.equal(downloadSites({...lcOnly, lc_mode: "off"}, settings).length, 0);
});

test("the edit form hides the LC fields of an F95 game while LC is off", () => {
  const f95 = "https://f95zone.to/threads/1/";
  assert.equal(showLcFields({ f95_url: f95, lc_url: url, lc_mode: "" }, false), false);
  assert.equal(showLcFields({ f95_url: f95, lc_url: url, lc_mode: "" }, true), true);
  assert.equal(showLcFields({ f95_url: f95, lc_url: url, lc_mode: "off" }, false), true);
  assert.equal(showLcFields({ f95_url: f95, lc_url: url, lc_mode: "on" }, false), true);
  assert.equal(showLcFields({ lc_url: url, lc_mode: "" }, false), true);
  assert.equal(showLcFields({ f95_url: f95 }, false), false);
  assert.equal(showLcFields({}, true), true);
});

test("the LC Plus row names the detected tier and why Check now is off", () => {
  assert.equal(lcPlusDetectedText("plus"), "Detected: Plus");
  assert.equal(lcPlusDetectedText("standard"), "Detected: Standard");
  assert.equal(lcPlusDetectedText(""), "Not detected yet");
  assert.equal(lcPlusCheckReason("ready", true), "");
  assert.match(lcPlusCheckReason("no_catalog", true), /catalog must be on/);
  assert.match(lcPlusCheckReason("no_extension", true), /wLib extension.*LewdCorner switch/);
  assert.match(lcPlusCheckReason("ready", false), /Use LewdCorner/);
  assert.equal(lcPlusCheckedAt(""), "");
  assert.equal(lcPlusCheckedAt("not a time"), "");
  assert.ok(lcPlusCheckedAt("2026-11-19T10:00:00Z", "en-US").includes("2026"));
});
