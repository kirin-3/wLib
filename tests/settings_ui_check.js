// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/settings_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-theme", "wlib-mock-platform"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", "[]");
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
      localStorage.removeItem("wlib-mock-settings");
    });
    const url = new URL(previousUrl); url.hash = "/settings";
    await page.goto(url.toString());
    await page.reload();
    await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
    const folder = page.getByLabel("Library folder", { exact: true });
    await folder.waitFor();

    // 1. Settings typed while a save is in flight must not be replaced by the response.
    await folder.fill("/tmp/first-folder");
    await page.evaluate(() => {
      window.__settingsBase = JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}");
      window.pywebview = {
        api: {
          save_settings: payload => new Promise(resolve => {
            setTimeout(() => {
              Object.assign(window.__settingsBase, payload);
              resolve({ success: true, mock: true });
            }, 900);
          }),
          get_settings: () => ({
            ...window.__settingsBase,
            rpgmaker_linux_runner_status: {
              available: false, path: "", source: "", configured_path: "", error: "not installed",
            },
          }),
        },
      };
    });
    const save = page.getByRole("button", { name: "Save Changes", exact: true });
    await save.click();
    await page.waitForFunction(() => document.querySelector(".save-bar")?.textContent.includes("Saving"));
    await folder.fill("/tmp/second-folder");
    await page.waitForFunction(() => window.__settingsBase?.library_folder === "/tmp/first-folder");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check(await folder.inputValue() === "/tmp/second-folder", "Edits typed during a settings save must stay in the form");
    check(await page.getByText("You have unsaved changes.", { exact: true }).isVisible(), "Edits typed during a settings save must stay marked unsaved");
    check(await save.isVisible(), "Edits typed during a settings save must leave Save available");

    // A retry without the delayed backend persists the newer value.
    await page.evaluate(() => { delete window.pywebview; });
    await save.click();
    await page.waitForFunction(() => !document.querySelector(".save-bar") || !document.querySelector(".save-bar").textContent.includes("Saving"));
    check(await folder.inputValue() === "/tmp/second-folder", "The retry must persist the newer settings value");
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}").library_folder === "/tmp/second-folder"),
      "The newer settings value must reach the backend",
    );

    // 2. The URM source is a path field and must show found/not-found feedback.
    await page.getByLabel("Universal Ren'Py Mod (URM) source file (.rpa)", { exact: true }).fill("/tmp/guide.rpa");
    await page.waitForFunction(() => {
      const input = document.querySelector("#urm-source");
      return input?.closest("section")?.querySelector(".path-status")?.textContent.includes("Found");
    });
    await page.getByLabel("Universal Ren'Py Mod (URM) source file (.rpa)", { exact: true }).fill("/tmp/missing-guide.rpa");
    await page.waitForFunction(() => {
      const input = document.querySelector("#urm-source");
      return input?.closest("section")?.querySelector(".path-status")?.textContent.includes("File not found");
    });

    // 3. Leaving Settings while an install status read is in flight must not
    // leave a polling loop running in the background.
    const discard = page.getByRole("button", { name: "Discard", exact: true });
    if (await discard.count()) await discard.click();
    await page.evaluate(() => {
      window.__installChecks = 0;
      window.pywebview = {
        api: {
          get_install_status: () => {
            window.__installChecks++;
            return new Promise(resolve => setTimeout(() => resolve({
              dlls_installed: false,
              rtps_installed: false,
              deps: { running: true, done: 0, total: 1, current: "checking", error: "" },
              rtps: { running: false, done: 0, total: 0, current: "", error: "" },
            }), 800));
          },
        },
      };
    });
    await page.getByRole("link", { name: "Library", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#/" || location.hash === "");
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.waitForFunction(() => window.__installChecks >= 1);
    const checksBeforeLeaving = await page.evaluate(() => window.__installChecks);
    await page.getByRole("link", { name: "Library", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#/" || location.hash === "");
    await page.waitForTimeout(3200);
    const checksAfterLeaving = await page.evaluate(() => window.__installChecks);
    check(
      checksAfterLeaving === checksBeforeLeaving,
      `Install polling continued after leaving Settings (${checksBeforeLeaving} -> ${checksAfterLeaving})`,
    );
    return "PASS: settings save-in-flight edits stay unsaved, URM path shows found/not-found feedback, install polling stops on leave";
  } finally {
    // Drop the test's own unsaved settings so the route guard does not block cleanup.
    const discard = page.getByRole("button", { name: "Discard", exact: true });
    if (await discard.count()) await discard.click().catch(() => {});
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of previous) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    if (previousViewport) await page.setViewportSize(previousViewport);
    await page.goto(previousUrl);
    await page.reload();
  }
}
