// SPDX-License-Identifier: GPL-3.0-or-later
// Library scale benchmark for the browser mock app. It is a measurement, not a
// pass/fail check, and does not run in CI. See "Library scale benchmarks" in
// docs/build.md. Build the UI, serve it with `npm run preview`, then:
//   playwright-cli -s=bench open "http://localhost:4173/?benchGames=1000#/"
//   playwright-cli -s=bench run-code --filename tests/library_scale_bench.js
//   playwright-cli -s=bench console info    (the results table)
// Query options: benchGames (default 1000), benchSeed (default 1), benchCommit.
async (page) => {
  const STORAGE_KEYS = ["wlib-mock-games", "wlib-library-view-state"];
  const COVER_ORIGIN = "http://wlib-bench.invalid";
  const COVER_SIZES = [[600, 338], [1280, 720], [1920, 1080]];
  const VARIANTS_PER_SIZE = 6;
  const VIEWPORT = { width: 1600, height: 1000 };
  const REPEATS = 3;

  const startUrl = new URL(page.url());
  if (!/^https?:$/.test(startUrl.protocol)) throw new Error("Open the wLib preview first, for example http://localhost:4173/#/");
  const option = (name, fallback) => {
    const raw = startUrl.searchParams.get(name);
    const value = raw === null ? NaN : Number(raw);
    return Number.isInteger(value) && value > 0 ? value : fallback;
  };
  const gameCount = option("benchGames", 1000);
  const seed = option("benchSeed", 1);
  const commit = startUrl.searchParams.get("benchCommit") || "";
  const libraryUrl = new URL(startUrl);
  libraryUrl.hash = "/";

  const previousViewport = page.viewportSize();
  const previousStorage = await page.evaluate(keys => Object.fromEntries(keys.map(key => [key, localStorage.getItem(key)])), STORAGE_KEYS);

  // --- Generated library (same fields as scripts/bench_library_scale.py) ---
  const makeGames = (count, seedValue) => {
    let state = seedValue >>> 0;
    const random = () => { // mulberry32
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const int = (low, high) => low + Math.floor(random() * (high - low + 1));
    const pick = list => list[Math.floor(random() * list.length)];
    const sample = (list, size) => {
      const copy = [...list];
      for (let i = copy.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [copy[i], copy[j]] = [copy[j], copy[i]]; }
      return copy.slice(0, size);
    };
    const engines = ["Ren'Py", "Unity", "RPGM", "HTML", "Unreal Engine", "Wolf RPG", "QSP", "Others"];
    const statuses = ["Not Started", "Plan to Play", "Playing", "Waiting For Update", "On Hold", "Completed", "Abandoned"];
    const words = ["Summer", "Night", "Academy", "Secret", "Lust", "Island", "Dream", "Shadow", "Crimson", "Village", "Office", "Legacy", "Hearts", "Echoes", "Witch", "Kingdom", "Station", "Desire", "Lost", "Frontier", "Harem", "Campus", "Neon", "Tower", "Fallen", "Pact", "Garden", "Mansion", "Rogue", "Signal"];
    const tagPool = ["", "male", "female", "3d", "2d", "big"].flatMap(a => ["protagonist", "romance", "sandbox", "corruption", "harem", "humor", "animated", "fantasy", "sci-fi", "voyeurism", "milf", "teasing", "vaginal sex", "oral sex", "groping", "handjob", "dating sim", "rpg", "school setting", "incest", "graphic violence", "management", "mobile game", "point & click", "multiple endings"].map(b => `${a} ${b}`.trim()));
    const now = Date.UTC(2026, 10, 1, 12);
    return Array.from({ length: count }, (_, index) => {
      const id = index + 1;
      const version = `0.${int(1, 30)}.${int(0, 9)}`;
      const played = random() < 0.6;
      const [width, height] = COVER_SIZES[index % COVER_SIZES.length];
      return {
        id,
        title: `${sample(words, int(2, 3)).join(" ")} ${String(index).padStart(4, "0")}`,
        exe_path: `/games/bench/${String(index).padStart(4, "0")}/Game.exe`,
        f95_url: `https://f95zone.to/threads/bench-${index}.${200000 + index}/`,
        version,
        latest_version: random() < 0.15 ? `0.${int(31, 40)}` : version,
        engine: pick(engines),
        developer: `Studio ${String(int(1, 150)).padStart(3, "0")}`,
        play_status: pick(statuses),
        tags: sample(tagPool, int(8, 20)).sort().join(", "),
        rating: (2.5 + random() * 2.5).toFixed(1),
        is_favorite: random() < 0.05,
        overview: Array.from({ length: int(80, 160) }, () => pick(words)).join(" ").toLowerCase(),
        screenshots: Array.from({ length: int(4, 10) }, (_, shot) => `https://attachments.f95zone.to/2026/0${int(1, 9)}/${200000 + index}_${shot}.jpg`),
        playtime_seconds: played ? int(600, 200000) : 0,
        last_played: played ? new Date(now - int(1, 8000) * 3600000).toISOString() : null,
        cover_image_path: `${COVER_ORIGIN}/cover/${id}-${width}x${height}.jpg`,
      };
    });
  };

  const results = {};
  const failures = [];
  try {
    await page.setViewportSize(VIEWPORT);

    // --- Cover images: drawn in the page once, served by the route per game ---
    const encoded = await page.evaluate(async ({ sizes, variants, seedValue }) => {
      let state = (seedValue * 7919) >>> 0;
      const random = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 4294967296; };
      const out = {};
      for (const [width, height] of sizes) {
        out[`${width}x${height}`] = [];
        for (let v = 0; v < variants; v++) {
          const smallW = Math.max(8, Math.floor(width / 12)), smallH = Math.max(8, Math.floor(height / 12));
          const small = new OffscreenCanvas(smallW, smallH);
          const pixels = new ImageData(smallW, smallH);
          for (let i = 0; i < pixels.data.length; i++) pixels.data[i] = (i & 3) === 3 ? 255 : Math.floor(random() * 256);
          small.getContext("2d").putImageData(pixels, 0, 0);
          const big = new OffscreenCanvas(width, height);
          const context = big.getContext("2d");
          context.imageSmoothingEnabled = true;
          context.imageSmoothingQuality = "high";
          context.drawImage(small, 0, 0, width, height);
          const blob = await big.convertToBlob({ type: "image/jpeg", quality: 0.85 });
          const bytes = new Uint8Array(await blob.arrayBuffer());
          let binary = "";
          for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
          out[`${width}x${height}`].push(btoa(binary));
        }
      }
      return out;
    }, { sizes: COVER_SIZES, variants: VARIANTS_PER_SIZE, seedValue: seed });
    const coverBytes = Object.fromEntries(Object.entries(encoded).map(([size, list]) => [size, list.map(b64 => Buffer.from(b64, "base64"))]));
    const coverMeans = Object.fromEntries(Object.entries(coverBytes).map(([size, list]) => [size, Math.round(list.reduce((sum, b) => sum + b.length, 0) / list.length)]));
    const coverRequests = new Set();
    await page.route(`${COVER_ORIGIN}/cover/**`, async (route) => {
      const match = /\/cover\/(\d+)-(\d+x\d+)\.jpg$/.exec(route.request().url());
      const list = match && coverBytes[match[2]];
      if (!list) return route.fulfill({ status: 404, body: "" });
      coverRequests.add(Number(match[1]));
      return route.fulfill({
        status: 200,
        contentType: "image/jpeg",
        headers: { "Cache-Control": "max-age=31536000, immutable", "Access-Control-Allow-Origin": "*" },
        body: list[Number(match[1]) % list.length],
      });
    });

    // --- Page helpers, installed before the app starts ---
    await page.addInitScript(() => {
      const bench = window.__wlibBenchRuntime = { longTasks: [], firstCardAt: null, firstCardCount: 0 };
      try {
        new PerformanceObserver(list => { for (const entry of list.getEntries()) bench.longTasks.push([entry.startTime, entry.duration]); })
          .observe({ type: "longtask", buffered: true });
      } catch { bench.longTasks = null; }
      const watchCards = () => {
        const observer = new MutationObserver(() => {
          const count = document.querySelectorAll(".game-card, .game-row").length;
          if (!count) return;
          bench.firstCardAt = performance.now();
          bench.firstCardCount = count;
          observer.disconnect();
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
      };
      if (document.documentElement) watchCards(); else document.addEventListener("readystatechange", watchCards, { once: true });
      const frame = () => new Promise(resolve => requestAnimationFrame(() => resolve()));
      const tick = () => new Promise(resolve => { const channel = new MessageChannel(); channel.port1.onmessage = () => resolve(); channel.port2.postMessage(0); });
      // Time from the action until the frame after the expected change is painted.
      bench.measure = async (action, done, timeoutMs = 15000) => {
        const start = performance.now();
        await action();
        while (!done()) {
          if (performance.now() - start > timeoutMs) return { failed: "expected result did not appear", start, end: performance.now() };
          await tick();
        }
        await frame();
        await frame();
        return { ms: performance.now() - start, start, end: performance.now() };
      };
      bench.longTasksBetween = (start, end) => (bench.longTasks || []).filter(([at]) => at >= start && at <= end);
      bench.resources = () => ({
        rendered_cards: document.querySelectorAll(".game-card, .game-row").length,
        dom_nodes: document.getElementsByTagName("*").length,
        js_heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null,
      });
      bench.frames = async (count) => { for (let i = 0; i < count; i++) await frame(); };
      // The library keeps only the cards near the screen, so the checks read
      // the selection count from the bulk bar, not from the cards.
      bench.selectedCount = () => Number(/\d+/.exec(document.querySelector(".bulk-count")?.textContent || "0")?.[0] || 0);
      bench.cardIds = () => [...document.querySelectorAll(".game-card, .game-row")].map(card => card.getAttribute("data-game-id"));
      bench.scroller = () => {
        let element = document.querySelector(".library-cards, .library-table")?.parentElement ?? null;
        while (element && !/(auto|scroll)/.test(getComputedStyle(element).overflowY)) element = element.parentElement;
        return element ?? document.scrollingElement;
      };
    });

    // --- Load the generated library ---
    const games = makeGames(gameCount, seed);
    const firstTitle = [...games].sort((a, b) => a.title.localeCompare(b.title))[0].title;
    await page.evaluate(({ games }) => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", JSON.stringify(games));
      localStorage.setItem("wlib-library-view-state", JSON.stringify({ layoutMode: "grid", sortBy: "title", sortDir: "asc", cardSize: 2, filterCollection: "All Games", isFiltersCollapsed: true }));
    }, { games });
    // A goto to the same URL only changes the hash; the reload runs the init script.
    await page.goto(libraryUrl.toString());
    await page.reload();
    await page.waitForFunction(() => !!window.__wlibBenchRuntime && window.__wlibBenchRuntime.firstCardAt !== null, null, { timeout: 60000 });
    // Covers near the screen load lazily; wait until those have decoded.
    const coversVisibleAt = await page.evaluate(async () => {
      const deadline = performance.now() + 30000;
      const visible = () => [...document.querySelectorAll(".game-card img:not(.cover-anim)")].filter(img => {
        const box = img.getBoundingClientRect();
        return box.bottom > 0 && box.top < innerHeight;
      });
      while (performance.now() < deadline) {
        const images = visible();
        if (images.length && images.every(img => img.complete && img.naturalWidth > 0)) break;
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      return performance.now();
    });
    const firstRender = await page.evaluate(() => {
      const bench = window.__wlibBenchRuntime;
      return { first_card_ms: bench.firstCardAt, first_card_count: bench.firstCardCount, long_tasks_ms: bench.longTasksBetween(0, bench.firstCardAt).map(([, d]) => Math.round(d)) };
    });
    results.first_render = {
      first_card_ms: Math.round(firstRender.first_card_ms),
      covers_on_screen_loaded_ms: Math.round(coversVisibleAt),
      long_tasks_ms: firstRender.long_tasks_ms,
      ...(await page.evaluate(() => window.__wlibBenchRuntime.resources())),
    };

    // --- Scroll to the end, one screen height at a time ---
    // The library order for "title, ascending": trimmed lower-case titles,
    // compared with numeric collation, then the game id (see sortGames).
    const sortedIds = await page.evaluate((list) => list
      .map(game => ({ id: game.id, key: String(game.title ?? "").trim().toLowerCase() }))
      .sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }) || a.id - b.id)
      .map(game => game.id), games.map(({ id, title }) => ({ id, title })));
    const scroll = await page.evaluate(async () => {
      const bench = window.__wlibBenchRuntime;
      const steps = [];
      const scroller = bench.scroller();
      const start = performance.now();
      let maxCards = 0, maxNodes = 0;
      for (let i = 0; i < 2000; i++) {
        const end = scroller.scrollHeight - scroller.clientHeight;
        if (scroller.scrollTop >= end - 1) break;
        const target = Math.min(end, scroller.scrollTop + scroller.clientHeight);
        const before = bench.cardIds().join();
        // The page updates in the scroll event, so a step is done two frames later.
        const result = await bench.measure(
          () => { scroller.scrollTop = target; scroller.dispatchEvent(new Event("scroll")); },
          () => true,
        );
        const now = bench.resources();
        maxCards = Math.max(maxCards, now.rendered_cards);
        maxNodes = Math.max(maxNodes, now.dom_nodes);
        steps.push({ cards: now.rendered_cards, changed: bench.cardIds().join() !== before, ms: Math.round(result.ms) });
      }
      const end = performance.now();
      return { steps, max_cards: maxCards, max_nodes: maxNodes, long_tasks_ms: bench.longTasksBetween(start, end).map(([, d]) => Math.round(d)) };
    });
    const stepTimes = scroll.steps.map(step => step.ms).sort((a, b) => a - b);
    results.scroll_to_end = {
      steps: scroll.steps.length,
      step_median_ms: stepTimes[Math.floor(stepTimes.length / 2)] ?? 0,
      step_max_ms: stepTimes.at(-1) ?? 0,
      total_ms: stepTimes.reduce((sum, ms) => sum + ms, 0),
      max_rendered_cards: scroll.max_cards,
      max_dom_nodes: scroll.max_nodes,
      long_tasks_count: scroll.long_tasks_ms.length,
      long_tasks_total_ms: scroll.long_tasks_ms.reduce((sum, d) => sum + d, 0),
      long_tasks_max_ms: Math.max(0, ...scroll.long_tasks_ms),
      ...(await page.evaluate(() => window.__wlibBenchRuntime.resources())),
    };
    const lastShown = await page.evaluate(id => !!document.querySelector(`[data-game-id="${id}"]`), sortedIds.at(-1));
    if (!lastShown) failures.push("scroll_to_end: the last game is not in the page at the end");

    // --- Actions. The library keeps only the cards near the screen, so runs
    // start at a known place: the end of the library (search, sort, layout,
    // card size) or the top (selection, refresh). Moving there is not timed.
    const scrollTo = (place) => page.evaluate(async (place) => {
      const bench = window.__wlibBenchRuntime;
      const scroller = bench.scroller();
      scroller.scrollTop = place === "end" ? scroller.scrollHeight : 0;
      scroller.dispatchEvent(new Event("scroll"));
      await bench.frames(3);
    }, place);
    // Scroll down from the top until the game's card is in the page.
    const scrollToGame = (id) => page.evaluate(async (id) => {
      const bench = window.__wlibBenchRuntime;
      const scroller = bench.scroller();
      const selector = `[data-game-id="${id}"]`;
      for (let i = 0; i < 2000 && !document.querySelector(selector); i++) {
        if (scroller.scrollTop >= scroller.scrollHeight - scroller.clientHeight - 1) break;
        scroller.scrollTop += scroller.clientHeight / 2;
        scroller.dispatchEvent(new Event("scroll"));
        await bench.frames(2);
      }
      document.querySelector(selector)?.scrollIntoView({ block: "center" });
      await bench.frames(3);
      return !!document.querySelector(selector);
    }, id);
    const measureOnce = (actionId, args = {}) => page.evaluate(async ({ actionId, args }) => {
      const bench = window.__wlibBenchRuntime;
      const cards = () => [...document.querySelectorAll(".game-card, .game-row")];
      const checked = () => bench.selectedCount();
      const button = label => document.querySelector(`button[aria-label="${label}"]`);
      const search = document.querySelector('input[aria-label="Search games"]');
      const setSearch = value => { search.value = value; search.dispatchEvent(new Event("input", { bubbles: true })); };
      const click = (element, init) => element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...init }));
      const actions = {
        search_narrow: () => bench.measure(() => setSearch(args.query), () => cards().length === args.expected),
        search_clear: () => bench.measure(() => setSearch(""), () => cards().length > args.narrowed),
        reverse_sort: () => {
          const first = cards()[0]?.getAttribute("data-game-id");
          return bench.measure(() => document.querySelector(".library-sort-dir").click(), () => cards()[0]?.getAttribute("data-game-id") !== first);
        },
        card_size: () => {
          const slider = document.querySelector('input[aria-label="Card size"]');
          const columns = getComputedStyle(document.querySelector(".library-cards")).gridTemplateColumns;
          const next = slider.value === "3" ? 2 : 3;
          return bench.measure(
            () => { slider.value = String(next); slider.dispatchEvent(new Event("input", { bubbles: true })); slider.dispatchEvent(new Event("change", { bubbles: true })); },
            () => getComputedStyle(document.querySelector(".library-cards")).gridTemplateColumns !== columns,
          );
        },
        ctrl_click_first: () => bench.measure(() => click(cards()[3], { ctrlKey: true }), () => checked() === 1),
        ctrl_click_second: () => bench.measure(() => click(cards()[5], { ctrlKey: true }), () => checked() === 2),
        shift_click_range: () => bench.measure(() => click(document.querySelector(`[data-game-id="${args.targetId}"]`), { shiftKey: true }), () => checked() === args.expected),
        clear_selection: () => bench.measure(() => document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })), () => checked() === 0),
        grid_to_list: () => bench.measure(() => button("List view").click(), () => document.querySelector(".game-row") !== null && document.querySelector(".game-card") === null),
        list_to_grid: () => bench.measure(() => button("Grid view").click(), () => document.querySelector(".game-card") !== null && document.querySelector(".game-row") === null),
        refresh: () => {
          const stored = JSON.parse(localStorage.getItem("wlib-mock-games"));
          const marker = `AAAA Refreshed ${Math.round(performance.now())}`;
          stored[0].title = marker;
          localStorage.setItem("wlib-mock-games", JSON.stringify(stored));
          return bench.measure(() => window.dispatchEvent(new Event("wlib-refresh-library")), () => (cards()[0]?.textContent || "").includes(marker));
        },
        // Only used to check that a wrong expected result is reported as failed.
        self_check: () => bench.measure(() => {}, () => false, 300),
      };
      const before = bench.resources().rendered_cards;
      const outcome = await actions[actionId]();
      return { ms: outcome.ms, failed: outcome.failed, cards_in_page: before, long_tasks_ms: bench.longTasksBetween(outcome.start, outcome.end).map(([, d]) => Math.round(d)) };
    }, { actionId, args });
    const runs = {};
    const record = (name, run) => { (runs[name] ||= []).push(run); };

    const selfCheck = await measureOnce("self_check");
    if (!selfCheck.failed) failures.push("self_check: a wrong expected result was not reported");

    // The page is at the end after the scroll test.
    for (let i = 0; i < REPEATS; i++) record("card_size", await measureOnce("card_size"));
    const rangeEnd = Math.min(400, gameCount - 1);
    for (let i = 0; i < REPEATS; i++) {
      await scrollTo("top");
      record("ctrl_click_first", await measureOnce("ctrl_click_first"));
      record("ctrl_click_second", await measureOnce("ctrl_click_second"));
      // Games 5..rangeEnd plus game 3, which stays selected.
      if (!(await scrollToGame(sortedIds[rangeEnd]))) failures.push("shift_click_range: target game not found");
      record("shift_click_range", await measureOnce("shift_click_range", { targetId: sortedIds[rangeEnd], expected: rangeEnd - 5 + 2 }));
      record("clear_selection", await measureOnce("clear_selection"));
    }
    for (let i = 0; i < REPEATS; i++) {
      await scrollTo("top");
      record("refresh", await measureOnce("refresh"));
    }
    // A unique 4-digit number from one title.
    const searchQuery = games[Math.floor(games.length / 2)].title.split(" ").at(-1);
    const searchExpected = games.filter(game => game.title.toLowerCase().includes(searchQuery.toLowerCase())).length;
    for (let i = 0; i < REPEATS; i++) {
      await scrollTo("end");
      record("search", await measureOnce("search_narrow", { query: searchQuery, expected: searchExpected }));
      record("search_clear", await measureOnce("search_clear", { narrowed: searchExpected }));
    }
    for (let i = 0; i < REPEATS; i++) {
      await scrollTo("end");
      record("reverse_sort", await measureOnce("reverse_sort"));
    }
    for (let i = 0; i < REPEATS; i++) {
      await scrollTo("end");
      record("grid_to_list", await measureOnce("grid_to_list"));
      await scrollTo("end");
      record("list_to_grid", await measureOnce("list_to_grid"));
    }
    for (const [name, list] of Object.entries(runs)) {
      const failed = list.find(run => run.failed);
      if (failed) {
        failures.push(name);
        results[name] = { failed: failed.failed, cards_in_page: failed.cards_in_page };
        continue;
      }
      const times = list.map(run => Math.round(run.ms));
      const sorted = [...times].sort((a, b) => a - b);
      results[name] = {
        median_ms: sorted[Math.floor(sorted.length / 2)],
        runs_ms: times,
        cards_in_page: Math.max(...list.map(run => run.cards_in_page)),
        long_tasks_max_ms: Math.max(0, ...list.flatMap(run => run.long_tasks_ms)),
      };
    }
    results.covers = { requests: coverRequests.size, mean_bytes_by_size: coverMeans };
  } finally {
    await page.unroute(`${COVER_ORIGIN}/cover/**`).catch(() => {});
    await page.evaluate(entries => {
      for (const [key, value] of Object.entries(entries)) {
        if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
      }
    }, previousStorage).catch(() => {});
    if (previousViewport) await page.setViewportSize(previousViewport).catch(() => {});
  }

  const report = {
    tool: "frontend",
    games: gameCount,
    seed,
    date: new Date().toISOString(),
    commit,
    browser: await page.evaluate(() => navigator.userAgent),
    viewport: VIEWPORT,
    failures,
    results,
  };
  const lines = [`wLib frontend benchmark: ${gameCount} games, seed ${seed}, commit ${commit || "?"}`, "-".repeat(72)];
  for (const [name, value] of Object.entries(results)) {
    if (name === "scroll_to_end") {
      lines.push(`${name.padEnd(22)} ${value.steps} steps, median ${value.step_median_ms} ms, max ${value.step_max_ms} ms; at the end ${value.rendered_cards} cards, ${value.dom_nodes} nodes, heap ${value.js_heap_mb} MB; most ${value.max_rendered_cards} cards, ${value.max_dom_nodes} nodes; long tasks ${value.long_tasks_count} (max ${value.long_tasks_max_ms} ms)`);
    } else if (name === "first_render") {
      lines.push(`${name.padEnd(22)} first card ${value.first_card_ms} ms, covers on screen ${value.covers_on_screen_loaded_ms} ms, ${value.rendered_cards} cards, ${value.dom_nodes} nodes, heap ${value.js_heap_mb} MB`);
    } else if (name === "covers") {
      lines.push(`${name.padEnd(22)} ${value.requests} requests, mean bytes ${JSON.stringify(value.mean_bytes_by_size)}`);
    } else if (value.failed) {
      lines.push(`${name.padEnd(22)} FAILED: ${value.failed}`);
    } else {
      lines.push(`${name.padEnd(22)} median ${String(value.median_ms).padStart(5)} ms  runs ${value.runs_ms.join("/")}  (${value.cards_in_page} cards in page, longest task ${value.long_tasks_max_ms} ms)`);
    }
  }
  lines.push("-".repeat(72));
  // run-code returns the JSON report; the table goes to the page console
  // (`playwright-cli console info`), and the report stays on window.__wlibBench.
  await page.evaluate(({ value, table }) => { window.__wlibBench = value; console.info(table); }, { value: report, table: lines.join("\n") });
  return report;
}
