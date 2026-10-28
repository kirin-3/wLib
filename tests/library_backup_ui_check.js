// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/library_backup_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const waitFor = async (condition, label) => {
    try {
      await page.waitForFunction(condition, null, { timeout: 10000 });
    } catch (error) {
      const state = await page.evaluate(() => ({ url: location.href, text: document.body.innerText.slice(-500) }));
      throw new Error(label + ": " + JSON.stringify(state));
    }
  };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-external", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", "[]");
      localStorage.setItem("wlib-theme", "dark");
    });
    const url = new URL(previousUrl); url.hash = "/import-export";
    await page.goto(url.toString());
    await page.reload();
    await page.getByRole("heading", { name: "Import / Export", exact: true }).waitFor();

    // 0. An existing folder is a valid export destination even though it is not
    // also the parent of a not-yet-created file.
    await page.evaluate(() => {
      window.pywebview = { api: { check_path: (path, kind) => Promise.resolve(
        kind === "parent" && path === "/tmp/export-folder"
          ? { ok: false, reason: "Expected a file" }
          : { ok: true, reason: "" },
      ) } };
    });
    await page.getByPlaceholder("Choose a folder or enter /path/to/wlib-export.json", { exact: true }).fill("/tmp/export-folder");
    await waitFor(() => {
      const input = document.querySelector('input[placeholder="Choose a folder or enter /path/to/wlib-export.json"]');
      return input?.closest(".backup-field")?.querySelector(".path-status")?.textContent.includes("Found");
    }, "an existing export folder does not show Found");
    await page.evaluate(() => { delete window.pywebview; });

    // 1. A late backup inspection must not fill the preview for a new path.
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await page.evaluate(() => {
      window.pywebview = {
        api: {
          inspect_library_backup: () => new Promise(resolve => {
            setTimeout(() => resolve({
              success: true,
              mock: true,
              manifest: { format: "wlib.library_migration", format_version: 1, exported_at: new Date().toISOString(), selected_sections: [] },
              available_sections: ["metadata", "user_state"],
              counts: { total_games: 5, matched_games: 0, new_games: 5, ambiguous_games: 0, warnings: 0 },
            }), 900);
          }),
        },
      };
    });
    const importPath = page.getByPlaceholder("/path/to/wlib-export.json", { exact: true });
    await importPath.fill("/tmp/a.json");
    await page.getByRole("button", { name: "Inspect Backup", exact: true }).click();
    await waitFor(() => [...document.querySelectorAll("button")].some(button => button.textContent.includes("Inspecting...")), "Inspect Backup does not show progress");
    await importPath.fill("/tmp/b.json");
    await page.waitForTimeout(1100);
    check(
      !(await page.getByText("Total: 5", { exact: false }).count()),
      "A late inspection for the previous path must not populate the new path's preview",
    );
    check(
      await page.getByRole("button", { name: "Import Selected", exact: true }).isDisabled(),
      "A late inspection must not enable Import Selected for a different path",
    );
    // A normal inspection still produces a preview.
    await page.evaluate(() => { delete window.pywebview; });
    await importPath.fill("/tmp/normal.json");
    await page.getByRole("button", { name: "Inspect Backup", exact: true }).click();
    await waitFor(() => ![...document.querySelectorAll("button")].find(button => button.textContent.includes("Import Selected"))?.disabled, "a normal backup inspection does not enable Import Selected");

    // 2. A late library inspection must not fill the preview for another source.
    await page.evaluate(() => {
      window.__lastDetect = "";
      window.pywebview = {
        api: {
          detect_external_library: source => new Promise(resolve => {
            setTimeout(() => {
              window.__lastDetect = source;
              resolve({ success: true, path: `/source/${source}/detected.db`, checked: [] });
            }, source === "xlibrary" ? 700 : 80);
          }),
          inspect_external_library: () => new Promise(resolve => {
            setTimeout(() => resolve({
              success: true,
              mock: true,
              available_sections: ["covers", "user_state", "executable_paths", "launch_targets"],
              counts: { total_games: 7, matched_games: 0, new_games: 7, ambiguous_games: 0, skipped_no_identity: 0, dropped_paths: 0, covers_to_copy: 0 },
              warnings: [],
            }), 900);
          }),
        },
      };
    });
    await page.getByRole("button", { name: "From another app", exact: true }).click();
    await page.getByText("Found library:", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Inspect Library", exact: true }).click();
    await waitFor(() => [...document.querySelectorAll("button")].some(button => button.textContent.includes("Inspecting...")), "Inspect Library does not show progress");
    await page.getByRole("button", { name: "f69", exact: true }).click();
    await page.waitForTimeout(1100);
    check(
      !(await page.getByText("New: 7", { exact: false }).count()),
      "A late inspection for the previous source must not populate another source's preview",
    );
    check(
      await page.getByRole("button", { name: "Import Selected", exact: true }).isDisabled(),
      "A late inspection must not enable Import Selected for a different source",
    );
    // A normal external inspection still produces a preview.
    await page.evaluate(() => { delete window.pywebview; });
    await page.getByRole("button", { name: "Inspect Library", exact: true }).click();
    await waitFor(() => ![...document.querySelectorAll("button")].find(button => button.textContent.includes("Import Selected"))?.disabled, "a normal external inspection does not enable Import Selected");

    // 3. Auto-detection must not overwrite a path typed while it was running.
    await page.evaluate(() => {
      window.__lastDetect = "";
      window.pywebview = { api: { detect_external_library: source => new Promise(resolve => {
        setTimeout(() => {
          window.__lastDetect = source;
          resolve({ success: true, path: `/source/${source}/detected.db`, checked: [] });
        }, 700);
      }) } };
    });
    const externalPath = page.getByPlaceholder("Path to the source library file", { exact: true });
    await page.getByRole("button", { name: "XLibrary", exact: true }).click();
    await waitFor(() => document.querySelector('input[placeholder="Path to the source library file"]')?.value === "", "source change does not clear the path");
    await externalPath.fill("/tmp/manual.db");
    await waitFor(() => window.__lastDetect === "xlibrary", "XLibrary detection did not run");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check(
      await externalPath.inputValue() === "/tmp/manual.db",
      "Auto-detection must not overwrite a path the user typed while it was running",
    );
    return "PASS: export folders show Found, late backup and external inspections stay with their path/source, auto-detection respects a typed path";
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      delete window.__lastDetect;
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
