// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/settings_load_failure_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const linuxCapabilities = {
    success: true,
    platform: "linux",
    wine_proton: true,
    wayland: true,
    cheat_engine_injection: false,
    rpgmaker_linux: false,
    companion_tools: false,
    runtime_installers: true,
    launch_modes: ["auto", "native", "wine_proton", "custom"],
    extension_dir: "",
  };
  const storedSettings = {
    proton_path: "/opt/wine",
    wine_prefix_path: "/opt/prefix",
    library_folder: "/srv/games",
    downloads_folder: "/srv/downloads",
    library_folder_pattern: "{creator}/{title}/{version}",
    download_detection_enabled: true,
    custom_play_statuses: [],
    collections: [],
    tag_colors: {},
    save_backup_keep: "all",
    enable_logging: false,
    rpgmaker_linux_runner_path: "",
    rpgmaker_linux_runner_status: {
      available: false,
      path: "",
      source: "",
      configured_path: "",
      error: "not installed",
    },
  };
  const openSettings = async () => {
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
  };
  const discardChanges = async () => {
    const discard = page.getByRole("button", { name: "Discard", exact: true });
    if (await discard.count()) await discard.click().catch(() => {});
  };
  try {
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", "[]");
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();
    await page.getByText("Your Library", { exact: false }).first().waitFor({ timeout: 5000 });

    // 1. A failed settings read must not leave an empty form that Save writes
    // over the stored values.
    await page.evaluate(({ linuxCapabilities, storedSettings }) => {
      window.__settingsAvailable = false;
      window.__saved = null;
      window.pywebview = {
        api: {
          get_platform_capabilities: () => Promise.resolve(linuxCapabilities),
          get_settings: () => window.__settingsAvailable
            ? Promise.resolve(storedSettings)
            : Promise.reject(new Error("database is locked")),
          save_settings: payload => { window.__saved = payload; return Promise.resolve({ success: true }); },
        },
      };
    }, { linuxCapabilities, storedSettings });
    await openSettings();
    const alert = page.getByRole("alert").filter({ hasText: "Could not load settings" });
    await alert.waitFor({ timeout: 5000 });

    const folder = page.getByLabel("Library folder", { exact: true });
    await folder.fill("/tmp/typed-after-failure");
    const save = page.getByRole("button", { name: "Save Changes", exact: true });
    await save.waitFor();
    check(await save.isDisabled(), "Save must stay disabled after the settings read failed");
    check(
      await page.evaluate(() => window.__saved === null),
      "A failed settings load must not let Save write defaults over stored settings",
    );

    // Retrying with a healthy backend restores the stored values and saving.
    await discardChanges();
    await page.evaluate(() => { window.__settingsAvailable = true; });
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("#settings-proton-path")?.value === "/opt/wine");
    check(!(await alert.isVisible().catch(() => false)), "A successful retry must clear the load error");
    await page.getByLabel("Library folder", { exact: true }).fill("/srv/library");
    await page.getByRole("button", { name: "Save Changes", exact: true }).click();
    await page.waitForFunction(() => window.__saved && window.__saved.library_folder === "/srv/library");
    const saved = await page.evaluate(() => window.__saved);
    check(saved.proton_path === "/opt/wine", "A retried save must keep the stored Proton path");
    check(saved.wine_prefix_path === "/opt/prefix", "A retried save must keep the stored Wine prefix");
    await discardChanges();

    // 2. A value typed while the settings read is still running must survive it.
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();
    await page.getByText("Your Library", { exact: false }).first().waitFor({ timeout: 5000 });
    await page.evaluate(linuxCapabilities => {
      window.__slowSettings = {
        proton_path: "/opt/wine",
        wine_prefix_path: "/opt/prefix",
        library_folder: "/srv/games",
        downloads_folder: "",
        library_folder_pattern: "{creator}/{title}/{version}",
        download_detection_enabled: true,
        custom_play_statuses: [],
        collections: [],
        tag_colors: {},
        save_backup_keep: "all",
        enable_logging: false,
        rpgmaker_linux_runner_path: "",
        rpgmaker_linux_runner_status: {
          available: false, path: "", source: "", configured_path: "", error: "not installed",
        },
      };
      window.pywebview = {
        api: {
          get_platform_capabilities: () => Promise.resolve(linuxCapabilities),
          get_settings: () => new Promise(resolve => setTimeout(() => resolve(window.__slowSettings), 2500)),
        },
      };
    }, linuxCapabilities);
    await openSettings();
    const slowFolder = page.getByLabel("Library folder", { exact: true });
    await slowFolder.fill("/tmp/typed-during-load");
    await page.waitForFunction(() => document.querySelector("#settings-proton-path")?.value === "/opt/wine", null, { timeout: 5000 });
    check(
      await slowFolder.inputValue() === "/tmp/typed-during-load",
      "A settings value typed before the read lands must not be replaced by the loaded value",
    );
    await page.getByText("You have unsaved changes.", { exact: true }).waitFor({ timeout: 3000 });

    return "PASS: settings load failures block unsafe saves, Retry restores saving, and edits typed during load survive";
  } finally {
    await discardChanges();
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
