// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/settings_runner_path_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", "[]");
      localStorage.setItem("wlib-mock-settings", JSON.stringify({
        rpgmaker_linux_runner_path: "",
        rpgmaker_linux_runner_status: {
          available: false,
          path: "",
          source: "",
          configured_path: "",
          error: "RPGMaker Linux runner is not installed or configured.",
        },
      }));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
    });
    await page.goto(new URL("/#/settings", previousUrl).toString());
    await page.reload();
    await page.getByRole("heading", { name: "RPGMaker Linux Runner", exact: true }).waitFor({ timeout: 10000 });

    await page.evaluate(() => {
      window.pywebview = {
        api: {
          check_path: (path, kind) => {
            const value = String(path || "").trim();
            if (value === "rpgmaker-linux") {
              return kind === "runner"
                ? { ok: true, reason: "" }
                : { ok: false, reason: "File not found" };
            }
            return { ok: false, reason: kind === "runner" ? "Command not found on PATH" : "File not found" };
          },
        },
      };
    });

    const runner = page.getByLabel("RPGMaker Linux Runner", { exact: true });
    const status = () => page.evaluate(() => {
      const input = document.querySelector('input[aria-labelledby="settings-rpgm-label"]');
      return input?.closest(".setting-row")?.querySelector(".path-status")?.textContent?.trim() || "";
    });

    // A command name on PATH is valid for this runner, like the Proton runner field.
    await runner.fill("rpgmaker-linux");
    await page.waitForFunction(() => {
      const input = document.querySelector('input[aria-labelledby="settings-rpgm-label"]');
      return input?.closest(".setting-row")?.querySelector(".path-status")?.textContent.includes("Found");
    }, null, { timeout: 3000 });
    check((await status()) === "Found", "A runner command name found on PATH must show Found");

    // A missing runner still reports the reason for a runner field.
    await runner.fill("definitely-missing-runner");
    await page.waitForFunction(() => {
      const input = document.querySelector('input[aria-labelledby="settings-rpgm-label"]');
      return input?.closest(".setting-row")?.querySelector(".path-status")?.textContent.includes("Command not found on PATH");
    }, null, { timeout: 3000 });

    await runner.fill("");
    return "PASS: the RPGMaker runner path field accepts a command name found on PATH";
  } finally {
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
