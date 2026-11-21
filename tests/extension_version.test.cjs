// Runs as part of `npm run test:unit` in ui/ (and therefore CI).
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');
const backgroundSource = readFileSync(path.join(__dirname, '..', 'extension', 'background.js'), 'utf8');
const parser = source.slice(source.indexOf('function extractGameInfo()'), source.indexOf('function getPlayStatusToneClass'));
const offlineText = 'Make sure wLib is open. If it is, reload the wLib extension in your browser.';

const extractFromTitle = (title, labels = [], postHtml = null) => {
  const heading = {
    textContent: title,
    querySelectorAll: (selector) => selector === 'a.labelLink'
      ? labels.map(textContent => ({ textContent })) : [],
    cloneNode() { return { textContent: title, querySelectorAll: () => [] }; },
  };
  const context = { getThreadIdentity: () => ({ canonicalUrl: '', id: 1 }), document: { querySelector: (selector) => {
    if (selector === 'h1.p-title-value') return heading;
    if (selector === '.message-body .bbWrapper' && postHtml !== null) {
      return { innerHTML: postHtml, textContent: postHtml.replace(/<[^>]+>/g, ' ') };
    }
    return null;
  }, querySelectorAll: () => [] }, console };
  return vm.runInNewContext(`${parser}\nextractGameInfo()`, context);
};

test('extension skips status prefixes when choosing the engine', () => {
  const info = extractFromTitle('Game [v1.0] [Dev]', ['Completed', "Ren'Py"]);
  assert.equal(info.engine, "Ren'Py");
  assert.equal(info.title, 'Game');
  assert.equal(extractFromTitle('Game [v1.0] [Dev]', ['VN', 'RPGM']).engine, 'RPGM');
});

test('wishlist message passes duplicate code and serializes tags', async () => {
  let listener;
  let request;
  vm.runInNewContext(backgroundSource, {
    chrome: { runtime: { onMessage: { addListener: (value) => { listener = value; } } } },
    fetch: async (url, options) => {
      request = { url, options };
      return {
        ok: false,
        status: 409,
        json: async () => ({ success: false, error: 'Already saved', error_code: 'duplicate_url', attached: true }),
      };
    },
    console,
    AbortController,
    setTimeout,
    clearTimeout,
  });
  const response = await new Promise(resolve => listener({
    action: 'wishlistGameInWLib',
    payload: { title: 'Demo', tags: ['sandbox', 'Ren\'Py'] },
  }, {}, resolve));
  assert.equal(request.url, 'http://localhost:8183/api/wishlist');
  assert.equal(request.options.headers['X-wLib-Extension'], '1');
  assert.equal(JSON.parse(request.options.body).tags, "sandbox, Ren'Py");
  assert.equal(response.error_code, 'duplicate_url');
  assert.equal(response.attached, true);
});

test('extension parses title versions exactly like the scraper', () => {
  // Shared with tests/test_scraper.py; a mismatch shows as a false "update available".
  const cases = JSON.parse(readFileSync(path.join(__dirname, 'version_title_cases.json'), 'utf8'));
  for (const [title, version] of cases) {
    assert.equal(extractFromTitle(title).version, version, title);
  }
});

test('extension keeps the last bracket as developer for non-numeric releases', () => {
  for (const [title, version] of [['Game [Final] [Dev]', 'Final'], ['Game [b12] [Dev]', 'b12'], ['Game [v1.2] [Dev]', '1.2'], ['Game [Dev]', '']]) {
    const info = extractFromTitle(title);
    assert.equal(info.version, version);
    assert.equal(info.developer, 'Dev');
    assert.equal(info.title, 'Game');
  }
});

test('extension parses post versions exactly like the scraper', () => {
  // Shared with tests/test_scraper.py; the scraper reports a missing version as "Unknown".
  const cases = JSON.parse(readFileSync(path.join(__dirname, 'post_version_cases.json'), 'utf8'));
  for (const [html, version] of cases) {
    assert.equal(extractFromTitle('Game', [], html).version || 'Unknown', version, html);
  }
});

const pageHelpers = source.slice(
  source.indexOf('const WLIB_UPDATES_PAGE_PATH'),
  source.indexOf('function normalizeCheckResult'),
);

const messageHelpers = source.slice(
  source.indexOf('function normalizeCheckResult'),
  source.indexOf('function initWLibExtension()'),
);

const runThreadCheck = (runtime, log = console) => {
  const context = { console: log, chrome: { runtime } };
  vm.createContext(context);
  vm.runInContext(messageHelpers, context);
  return vm.runInContext(
    'checkGameInWLib("https://f95zone.to/threads/game.123/", [])',
    context,
  );
};

test('thread check survives a reload that invalidated the extension context', async () => {
  // Chrome throws synchronously when the auto-update reload invalidated the
  // content script. The check must resolve (so the widget still injects and
  // can show "extension updated. Refresh this page.") instead of rejecting.
  const result = await runThreadCheck({
    id: undefined,
    sendMessage() {
      throw new Error('Extension context invalidated.');
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    exists: false,
    playStatus: '',
    wishlisted: false,
    appOutdated: false,
    reachable: false,
    failureText: 'wLib extension updated. Refresh this page.',
  });
});

test('thread check treats a missing background receiver as a failed check', async () => {
  const result = await runThreadCheck({
    id: 'test',
    get lastError() {
      return { message: 'Receiving end does not exist.' };
    },
    sendMessage(_message, callback) {
      callback(undefined);
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    exists: false,
    playStatus: '',
    wishlisted: false,
    appOutdated: false,
    reachable: false,
    failureText: 'Could not contact the wLib extension. Refresh this page.',
  });
});

test('thread check preserves offline text and logs it at debug level', async () => {
  const calls = [];
  const result = await runThreadCheck({
    id: 'test',
    sendMessage(_message, callback) {
      callback({ success: false, failure: 'offline', error: offlineText });
    },
  }, {
    debug: () => calls.push('debug'),
    warn: () => calls.push('warn'),
  });
  assert.equal(result.reachable, false);
  assert.equal(result.failureText, offlineText);
  assert.deepEqual(calls, ['debug']);
});

test('thread check still normalizes a successful response', async () => {
  const result = await runThreadCheck({
    id: 'test',
    lastError: null,
    sendMessage(_message, callback) {
      callback({
        success: true,
        data: {
          exists: true,
          canAttachThread: true,
          wishlisted: false,
          playStatus: 'Playing',
          appOutdated: false,
        },
      });
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    exists: true,
    canAttachThread: true,
    playStatus: 'Playing',
    wishlisted: false,
    appOutdated: false,
    reachable: true,
  });
});

const tileHelpers = source.slice(0, source.indexOf('function ensureLibraryBadge'));

const createTilePage = (urls) => {
  let clock = 1000;
  let nextTimer = 1;
  const timers = new Map();
  const badges = new Set();
  const requests = [];
  let respond = () => ({ success: false, failure: 'offline', error: offlineText });
  class FakeElement {
    constructor() { this.dataset = {}; this.classList = { add() {} }; }
  }
  class FakeAnchor extends FakeElement {
    constructor(href) {
      super();
      this.href = href;
      this.thumb = new FakeElement();
      this.thumb.url = href;
    }
    querySelector(selector) { return selector === '.resource-tile_thumb' ? this.thumb : null; }
  }
  const tiles = urls.map(url => new FakeAnchor(url));
  const context = {
    console: { debug() {}, warn() {}, error() {} },
    URL,
    Date: { now: () => clock },
    HTMLAnchorElement: FakeAnchor,
    HTMLElement: FakeElement,
    window: {
      location: { href: 'https://f95zone.to/sam/latest_alpha/', origin: 'https://f95zone.to', pathname: '/sam/latest_alpha/' },
      setTimeout(fn, delay) { const id = nextTimer++; timers.set(id, { fn, at: clock + delay, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    document: { contains: () => true, querySelectorAll: () => tiles },
    chrome: {
      runtime: {
        id: 'test',
        lastError: null,
        sendMessage(message, callback) {
          requests.push(message.url);
          callback(respond(message));
        },
      },
    },
    ensureLibraryBadge: (thumb) => badges.add(thumb.url),
    removeLibraryBadge: (thumb) => badges.delete(thumb.url),
  };
  vm.createContext(context);
  vm.runInContext(tileHelpers, context);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const runNextTimer = async () => {
    const [id, timer] = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
    timers.delete(id);
    clock = Math.max(clock, timer.at);
    timer.fn();
    await flush();
  };
  return {
    badges,
    requests,
    pendingDelays: () => [...timers.values()].map(timer => timer.delay),
    setResponse(fn) { respond = fn; },
    async scan() { vm.runInContext('scanLatestAlphaTiles()', context); await flush(); },
    runNextTimer,
    async runTimers() { while (timers.size) await runNextTimer(); },
  };
};

test('latest-alpha badges appear once wLib starts after the page', async () => {
  const urls = ['https://f95zone.to/threads/one.1/', 'https://f95zone.to/threads/two.2/'];
  const page = createTilePage(urls);

  await page.scan();
  assert.equal(page.badges.size, 0);
  assert.deepEqual(page.pendingDelays(), [5000]);

  // Mutations while wLib is still down must not re-check every tile.
  await page.scan();
  assert.equal(page.requests.length, 2);

  // The retry timer queues the normal 60 ms scan, which re-checks the tiles.
  page.setResponse(() => ({ success: true, data: { exists: true } }));
  await page.runTimers();
  assert.deepEqual([...page.badges].sort(), urls);

  // Successful answers are cached, so a later scan sends nothing new.
  const sent = page.requests.length;
  await page.scan();
  assert.equal(page.requests.length, sent);
});

test('latest-alpha retries back off while wLib stays closed', async () => {
  const page = createTilePage(['https://f95zone.to/threads/one.1/']);
  await page.scan();
  assert.deepEqual(page.pendingDelays(), [5000]);
  // Each round is the retry timer followed by the scan timer it queues.
  await page.runNextTimer();
  await page.runNextTimer();
  assert.deepEqual(page.pendingDelays(), [10000]);
  for (let round = 0; round < 8; round += 1) {
    await page.runNextTimer();
    await page.runNextTimer();
  }
  assert.deepEqual(page.pendingDelays(), [60000]);
  assert.equal(page.badges.size, 0);
});

const runPageHelper = (code, pathname = '/') => {
  const context = {
    window: { location: { href: `https://f95zone.to${pathname}`, origin: 'https://f95zone.to', pathname } },
    document: {},
    chrome: {},
    console,
    URL,
  };
  vm.createContext(context);
  vm.runInContext(pageHelpers, context);
  return vm.runInContext(code, context);
};

test('latest-alpha badges tolerate a missing trailing slash', () => {
  // The manifest match (sam/latest_alpha*) fires with and without the slash.
  assert.equal(runPageHelper('isLatestAlphaPage()', '/sam/latest_alpha/'), true);
  assert.equal(runPageHelper('isLatestAlphaPage()', '/sam/latest_alpha'), true);
  assert.equal(runPageHelper('isLatestAlphaPage()', '/threads/game.123/'), false);
  assert.equal(runPageHelper('isThreadPage()', '/threads/game.123/'), true);
});

test('thread identity rejects non-F95 hosts like the backend does', () => {
  const f95 = runPageHelper(`getThreadIdentity('https://f95zone.to/threads/game.123/page-2')`);
  assert.equal(f95.id, '123');
  assert.equal(f95.canonicalUrl, 'https://f95zone.to/threads/game.123/');
  const www = runPageHelper(`getThreadIdentity('https://www.f95zone.to/threads/456/')`);
  assert.equal(www.id, '456');
  // core/f95zone.py extract_thread_id returns '' off F95 hosts; the content
  // script must agree instead of checking foreign /threads/<id>/ URLs.
  const foreign = runPageHelper(`getThreadIdentity('https://example.com/threads/123/')`);
  assert.equal(foreign.id, '');
});

test('cover fallback prefers lazy-load data-src over placeholder src', () => {
  const lazyImg = {
    src: 'data:image/gif;base64,placeholder',
    getAttribute: (name) => (name === 'data-src' ? 'https://attachments.f95zone.to/real.jpg' : null),
  };
  const plainImg = { src: 'https://attachments.f95zone.to/plain.jpg', getAttribute: () => null };
  const extractWithCover = (img) => {
    const context = {
      getThreadIdentity: () => ({ canonicalUrl: 'https://f95zone.to/threads/game.123/', id: '123' }),
      document: {
        querySelector: (selector) => {
          if (selector === 'h1.p-title-value') {
            return {
              textContent: 'Game [Dev]',
              querySelectorAll: () => [],
              cloneNode() { return { textContent: 'Game [Dev]', querySelectorAll: () => [] }; },
            };
          }
          if (selector === '.message-inner') return { querySelector: () => img };
          return null;
        },
        querySelectorAll: () => [],
      },
      console,
    };
    return vm.runInNewContext(`${parser}\nextractGameInfo()`, context);
  };
  assert.equal(extractWithCover(lazyImg).coverImage, 'https://attachments.f95zone.to/real.jpg');
  assert.equal(extractWithCover(plainImg).coverImage, 'https://attachments.f95zone.to/plain.jpg');
});

const runBackground = (stubs) => {
  const context = {
    chrome: { runtime: { onMessage: { addListener: () => {} } } },
    fetch: async () => { throw new Error('fetch stub not set'); },
    console,
    AbortController,
    setTimeout,
    clearTimeout,
    ...stubs,
  };
  vm.createContext(context);
  vm.runInContext(backgroundSource, context);
  return context;
};

const rejectFetch = error => async () => { throw error; };
const jsonReply = (status, data) => async () => new Response(JSON.stringify(data), { status });

for (const [label, fetch, failure, userText, level, detail, action = 'checkGameInWLib'] of [
  ['offline', rejectFetch(new TypeError('Failed to fetch')), 'offline', offlineText, 'debug', 'TypeError: Failed to fetch'],
  ['timeout', (_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason));
  }), 'timeout', 'wLib did not answer. Try again.', 'warn', 'AbortError: This operation was aborted'],
  ['foreign', async () => new Response('<html>Other program</html>'), 'foreign', 'Another program answered on port 8183, not wLib.', 'warn', 'SyntaxError:'],
  ['starting', jsonReply(503, { error: 'Application is not ready' }), 'server', 'Application is not ready', 'warn', 'WLibServerError: HTTP 503: Application is not ready'],
  ['server without error text', jsonReply(500, {}), 'server', 'wLib could not do this. Check the wLib log.', 'warn', 'WLibServerError: HTTP 500'],
  ['server failure with HTTP 200', jsonReply(200, { success: false, error: 'Could not check game' }), 'server', 'Could not check game', 'warn', 'WLibServerError: HTTP 200: Could not check game'],
  ['unexpected', rejectFetch(new Error('Unexpected problem')), 'offline', offlineText, 'warn', 'Error: Unexpected problem'],
  ['code TypeError after fetch', async () => ({ ok: true, status: 200, json: async () => { throw new TypeError('bug'); } }), 'offline', offlineText, 'warn', 'TypeError: bug'],
  ['offline wishlist', rejectFetch(new TypeError('NetworkError when attempting to fetch resource.')), 'offline', offlineText, 'debug', 'TypeError: NetworkError when attempting to fetch resource.', 'wishlistGameInWLib'],
]) {
  test(`extension reports ${label} without exposing diagnostics in the widget text`, async () => {
    let listener;
    const logs = [];
    runBackground({
      chrome: { runtime: { onMessage: { addListener: value => { listener = value; } } } },
      fetch,
      setTimeout: callback => setTimeout(callback, 5),
      console: Object.fromEntries(['debug', 'warn', 'error'].map(method => [
        method, (...args) => logs.push([method, ...args]),
      ])),
    });
    const response = await new Promise(resolve => listener({
      action,
      url: 'https://f95zone.to/threads/game.123/',
      payload: {},
    }, {}, resolve));
    assert.equal(response.success, false);
    assert.equal(response.failure, failure);
    assert.equal(response.error, userText);
    assert.ok(response.detail.startsWith(detail), response.detail);
    assert.deepEqual(logs, [[level, `wLib ${action} (${failure}):`, response.detail]]);
  });
}

test('version comparison orders releases and ignores v-prefix/suffix', () => {
  const context = runBackground({});
  const compare = (a, b) => vm.runInContext(`compareVersions(${JSON.stringify(a)}, ${JSON.stringify(b)})`, context);
  assert.equal(compare('1.0.12', '1.0.2'), 1);
  assert.equal(compare('0.3.8', '0.3.9'), -1);
  assert.equal(compare('1.0.12', '1.0.12'), 0);
  assert.equal(compare('v0.3.9', '0.3.9'), 0);
  assert.equal(compare('0.4.0-rc1', '0.4.0'), 0);
  assert.equal(compare('', '0.3.9'), -1);
});

test('desktop requests abort instead of hanging forever', async () => {
  let captured;
  const context = runBackground({
    fetch: (url, options) => {
      captured = { url, options };
      return new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('Aborted');
          error.name = 'AbortError';
          reject(error);
        });
      });
    },
  });
  await assert.rejects(
    vm.runInContext('fetchWLibJson("http://localhost:8183/api/check?url=x", undefined, 20)', context),
    (error) => error && error.name === 'AbortError',
  );
  assert.ok(captured.options.signal instanceof AbortSignal);
});

test('check request carries an abort signal so a stuck app cannot wedge the widget', async () => {
  let listener;
  let captured;
  const context = runBackground({
    chrome: { runtime: { onMessage: { addListener: (value) => { listener = value; } } } },
    fetch: async (url, options) => {
      captured = { url, options };
      return { ok: true, status: 200, json: async () => ({ exists: false }) };
    },
  });
  const checkResponse = await new Promise((resolve) => listener(
    { action: 'checkGameInWLib', url: 'https://f95zone.to/threads/game.123/' }, {}, resolve,
  ));
  assert.equal(checkResponse.success, true);
  assert.ok(captured.options.signal instanceof AbortSignal);
  assert.equal(captured.options.signal.aborted, false);
});

test('timeout also covers a response body that never finishes', async () => {
  const context = runBackground({
    fetch: (url, options) => Promise.resolve({
      ok: true,
      status: 200,
      json: () => new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          const error = new Error('Aborted');
          error.name = 'AbortError';
          reject(error);
        });
      }),
    }),
  });
  await assert.rejects(
    vm.runInContext('fetchWLibJson("http://localhost:8183/api/check?url=x", undefined, 20)', context),
    (error) => error && error.name === 'AbortError',
  );
});

test('manifest 1.1.x grants f95 host and alarms without tabs or cookies', () => {
  const manifest = JSON.parse(readFileSync(path.join(__dirname, '..', 'extension', 'manifest.json'), 'utf8'));
  assert.equal(manifest.version, '1.2.14');
  assert.ok((manifest.permissions || []).includes('storage'));
  assert.ok((manifest.permissions || []).includes('alarms'));
  assert.ok(!(manifest.permissions || []).includes('tabs'));
  assert.ok(!(manifest.permissions || []).includes('cookies'));
  assert.ok((manifest.host_permissions || []).includes('http://localhost:8183/*'));
  assert.ok((manifest.host_permissions || []).includes('https://f95zone.to/*'));
});

test('bridge allowlist accepts thread and batch URLs', async () => {
  const calls = [];
  const context = runBackground({
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { status: 200, url, text: async () => '<html>ok</html>' };
    },
    URL,
    TextEncoder,
    TextDecoder,
  });
  const allowedThread = await vm.runInContext(
    'bridgeFetchUrl("https://f95zone.to/threads/12345/")',
    context,
  );
  assert.equal(allowedThread.ok, true);
  assert.equal(allowedThread.status, 200);
  assert.equal(allowedThread.body, '<html>ok</html>');
  assert.equal(calls[0].options.credentials, 'include');
  assert.equal(calls[0].options.method, 'GET');

  const allowedBatch = await vm.runInContext(
    'bridgeFetchUrl("https://f95zone.to/sam/checker.php?threads=1,2,3")',
    context,
  );
  assert.equal(allowedBatch.ok, true);
  assert.equal(calls.length, 2);
});

test('bridge refuses non-allowlisted URLs without fetching', async () => {
  let fetched = false;
  const context = runBackground({
    fetch: async () => {
      fetched = true;
      throw new Error('must not fetch');
    },
    URL,
    TextEncoder,
    TextDecoder,
  });
  const refused = await vm.runInContext(
    'bridgeFetchUrl("https://example.com/")',
    context,
  );
  assert.equal(refused.ok, false);
  assert.equal(refused.error, 'refused');
  assert.equal(fetched, false);
  // The fetch bridge only looks up F95 and LC tabs for its logged-in fallback;
  // optional-site registration may inject into already-open pages after an
  // explicit permission grant.
  const bridgeSource = backgroundSource.slice(backgroundSource.indexOf('// ---- Extension fetch fallback'), backgroundSource.indexOf('// Optional sites'));
  assert.deepEqual(bridgeSource.match(/chrome\.tabs\.\w+\([^)]*\)/g), ['chrome.tabs.query({ url: tabSite.tabs })']);
  assert.deepEqual(
    JSON.parse(JSON.stringify(vm.runInContext('BRIDGE_TAB_SITES', context))),
    {
      f95: { host: 'f95zone.to', tabs: 'https://f95zone.to/*' },
      lc: { host: 'lewdcorner.com', tabs: 'https://lewdcorner.com/*' },
    },
  );
  // Resolve requests may open windows; fetch itself stays tab-free.
  assert.ok(!backgroundSource.includes('chrome.offscreen'));
  assert.ok(!backgroundSource.includes('chrome.cookies'));
});

test('bridge rejects off-site redirects without the body', async () => {
  const context = runBackground({
    fetch: async () => ({
      status: 200,
      url: 'https://example.com/',
      text: async () => 'evil',
    }),
    URL,
    TextEncoder,
    TextDecoder,
  });
  const result = await vm.runInContext(
    'bridgeFetchUrl("https://f95zone.to/threads/12345/")',
    context,
  );
  assert.equal(result.ok, false);
  assert.equal(result.error, 'offsite_redirect');
  assert.equal(result.body, undefined);
});

test('bridge refuses oversized bodies', async () => {
  const big = 'x'.repeat(4 * 1024 * 1024 + 1);
  const context = runBackground({
    fetch: async () => ({
      status: 200,
      url: 'https://f95zone.to/threads/12345/',
      text: async () => big,
    }),
    URL,
    TextEncoder,
    TextDecoder,
  });
  const result = await vm.runInContext(
    'bridgeFetchUrl("https://f95zone.to/threads/12345/")',
    context,
  );
  assert.equal(result.ok, false);
  assert.equal(result.error, 'too_large');
});

test('bridge keeps multi-byte characters split across stream chunks', async () => {
  const encoded = new TextEncoder().encode('ゲーム [v1.0]');
  const split = 2; // Inside the first three-byte character.
  const chunks = [
    new Uint8Array(encoded.slice(0, split)),
    new Uint8Array(encoded.slice(split)),
  ];
  const body = {
    getReader() {
      let index = 0;
      return {
        async read() {
          return index < chunks.length
            ? { done: false, value: chunks[index++] }
            : { done: true, value: undefined };
        },
        async cancel() {},
        releaseLock() {},
      };
    },
  };
  const context = runBackground({
    fetch: async () => ({
      status: 200,
      url: 'https://f95zone.to/threads/12345/',
      body,
    }),
    URL,
    TextEncoder,
    TextDecoder,
  });
  const result = await vm.runInContext(
    'bridgeFetchUrl("https://f95zone.to/threads/12345/")',
    context,
  );
  assert.equal(result.ok, true);
  assert.equal(result.body, 'ゲーム [v1.0]');
});

test('bridge fetch aborts on timeout', async () => {
  const context = runBackground({
    fetch: (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('Aborted');
        error.name = 'AbortError';
        reject(error);
      });
    }),
    URL,
    TextEncoder,
    TextDecoder,
  });
  const result = await vm.runInContext('bridgeFetchUrl("https://f95zone.to/threads/12345/", 20)', context);
  assert.equal(result.ok, false);
  assert.equal(result.error, 'timeout');
});

const makeBridgeHarness = ({ canFetch = true, firefox = false } = {}) => {
  const storageWrites = [];
  const storageListeners = [];
  const sent = [];
  const instances = [];
  const timeouts = [];
  const intervals = [];
  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.sent = sent;
      instances.push(this);
    }
    send(data) {
      sent.push(data);
    }
    close() {
      this.readyState = 3;
      if (this.onclose) this.onclose();
    }
  }
  const chromeStub = {
    runtime: {
      onMessage: { addListener: () => {} },
      getManifest: () => ({ version: '1.2.0' }),
      onStartup: { addListener: () => {} },
      onInstalled: { addListener: () => {} },
    },
    alarms: {
      create: () => {},
      onAlarm: { addListener: () => {} },
    },
    permissions: {
      contains: async () => canFetch,
      onAdded: { addListener: () => {} },
      onRemoved: { addListener: () => {} },
    },
    storage: {
      local: { set: async (items) => { storageWrites.push(items); } },
      onChanged: { addListener: (fn) => storageListeners.push(fn) },
    },
  };
  const context = {
    chrome: chromeStub,
    ...(firefox ? { browser: { runtime: { getBrowserInfo: async () => ({ name: 'Firefox' }) } } } : {}),
    WebSocket: FakeWebSocket,
    fetch: async () => ({
      status: 200,
      url: 'https://f95zone.to/threads/12345/',
      text: async () => '<html>ok</html>',
    }),
    console,
    AbortController,
    URL,
    TextEncoder,
    TextDecoder,
    setTimeout: (fn, ms) => {
      timeouts.push({ fn, ms });
      return timeouts.length;
    },
    clearTimeout: () => {},
    setInterval: (fn, ms) => {
      intervals.push({ fn, ms });
      return intervals.length;
    },
    clearInterval: () => {},
  };
  vm.createContext(context);
  vm.runInContext(backgroundSource, context);
  // No stored pairing token: the socket opens at once, without ?token=.
  vm.runInContext('bridgeToken = ""', context);
  return { context, sent, instances, timeouts, intervals, storageWrites, storageListeners };
};

test('bridge keeps the Firefox event page alive only while connected', () => {
  const { context, instances, intervals, storageWrites, storageListeners } = makeBridgeHarness({ firefox: true });
  assert.equal(storageListeners.length, 1);
  vm.runInContext('ensureBridge()', context);
  assert.equal(storageWrites.length, 0);
  instances[0].readyState = 1;
  instances[0].onopen();
  assert.equal(storageWrites.length, 1);
  assert.ok(intervals[0].ms < 30000);
  intervals[0].fn();
  assert.equal(storageWrites.length, 2);
  assert.ok(typeof storageWrites[1].wlibBridgeKeepalive === 'number');
  instances[0].readyState = 3;
  intervals[0].fn();
  assert.equal(storageWrites.length, 2);
});

test('bridge skips the storage keepalive outside Firefox', () => {
  const { context, instances, intervals, storageWrites } = makeBridgeHarness();
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  instances[0].onopen();
  intervals[0].fn();
  assert.equal(storageWrites.length, 0);
});

test('bridge hello carries protocol and can_fetch', async () => {
  const { context, sent, instances } = makeBridgeHarness({ canFetch: true });
  vm.runInContext('ensureBridge()', context);
  assert.equal(instances.length, 1);
  instances[0].readyState = 1;
  instances[0].onopen();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const hello = sent.map((raw) => JSON.parse(raw)).find((msg) => msg.type === 'hello');
  assert.ok(hello);
  assert.equal(hello.protocol, 2);
  assert.equal(hello.can_resolve, true);
  assert.equal(hello.extension_version, '1.2.0');
  assert.equal(hello.can_fetch, true);
});

test('bridge fetch-request dispatches to fetch_result', async () => {
  const { context, sent, instances } = makeBridgeHarness({ canFetch: true });
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  instances[0].onopen();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  sent.length = 0;
  instances[0].onmessage({
    data: JSON.stringify({ type: 'fetch', id: 'abc123', url: 'https://f95zone.to/threads/12345/' }),
  });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const result = sent.map((raw) => JSON.parse(raw)).find((msg) => msg.type === 'fetch_result');
  assert.ok(result);
  assert.equal(result.id, 'abc123');
  assert.equal(result.ok, true);
  assert.equal(result.body, '<html>ok</html>');
});

test('bridge reconnects after close with backoff and no storm', async () => {
  const { context, instances, timeouts } = makeBridgeHarness({ canFetch: true });
  vm.runInContext('ensureBridge()', context);
  assert.equal(instances.length, 1);
  instances[0].readyState = 1;
  instances[0].onopen();
  await new Promise((resolve) => setImmediate(resolve));
  const timersBefore = timeouts.length;
  instances[0].onclose();
  assert.ok(timeouts.length > timersBefore);
  assert.equal(timeouts[timeouts.length - 1].ms, 5000);
  // Second close while a reconnect is pending must not schedule another.
  const timersAfterFirstClose = timeouts.length;
  vm.runInContext('ensureBridge()', context);
  // Still one socket (connecting/open guard) and no extra timer storm:
  // ensureBridge with a pending reconnect timer must not create sockets in a loop.
  assert.ok(instances.length <= 2);
  assert.ok(timeouts.length - timersAfterFirstClose <= 1);
});

test('manifest CSP keeps the localhost bridge on plain ws', () => {
  const manifest = JSON.parse(readFileSync(path.join(__dirname, '..', 'extension', 'manifest.json'), 'utf8'));
  const policy = manifest.content_security_policy && manifest.content_security_policy.extension_pages;
  assert.ok(policy && policy.includes("script-src 'self'"));
  // Firefox's MV3 default includes this directive, which turns ws://localhost into wss://.
  assert.ok(!policy.includes('upgrade-insecure-requests'));
});

const GUEST_PAGE = '<html id="XF" data-logged-in="false"><title>Log in</title></html>';
const MEMBER_PAGE = '<html id="XF" data-logged-in="true"><h1>Game</h1></html>';

const makeTabFetchHarness = ({ backgroundBody, tabBody, tabs = [{ id: 7 }] }) => {
  const calls = { background: 0, tab: 0, injected: [] };
  const context = runBackground({
    fetch: async (url) => {
      calls.background += 1;
      return { status: 200, url, text: async () => backgroundBody };
    },
    URL,
    TextEncoder,
    TextDecoder,
  });
  context.chrome.tabs = { query: async () => tabs };
  context.chrome.scripting = {
    executeScript: async (options) => {
      calls.tab += 1;
      calls.injected.push(options);
      return [{ result: { ok: true, status: 200, final_url: options.args[0], body: tabBody } }];
    },
  };
  return { context, calls };
};

test('guest background fetch is repeated from an open F95 tab, which is then preferred', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE });
  const first = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/61053/")', context);
  assert.equal(first.body, MEMBER_PAGE);
  assert.deepEqual([calls.background, calls.tab], [1, 1]);
  assert.equal(calls.injected[0].target.tabId, 7);
  assert.equal(calls.injected[0].args[0], 'https://f95zone.to/threads/61053/');

  const second = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/1/")', context);
  assert.equal(second.body, MEMBER_PAGE);
  // The tab goes first now; the background is not asked again.
  assert.deepEqual([calls.background, calls.tab], [1, 2]);
});

test('challenged background fetch is repeated from an open F95 tab', async () => {
  const challenge = '<!DOCTYPE html><html><head><title>Just a moment...</title></head><body><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script></body></html>';
  const { context, calls } = makeTabFetchHarness({ backgroundBody: challenge, tabBody: MEMBER_PAGE });
  const result = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/321017/")', context);
  assert.equal(result.body, MEMBER_PAGE);
  assert.deepEqual([calls.background, calls.tab], [1, 1]);
});

test('passive Cloudflare probe on a member page does not trigger the tab retry', async () => {
  const page = '<html id="XF" data-logged-in="true"><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></html>';
  const { context, calls } = makeTabFetchHarness({ backgroundBody: page, tabBody: MEMBER_PAGE });
  await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/1/")', context);
  assert.equal(calls.tab, 0);
});

test('guest background fetch is kept when no F95 tab is open', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE, tabs: [] });
  const result = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/61053/")', context);
  assert.equal(result.body, GUEST_PAGE);
  assert.equal(calls.tab, 0);
  assert.equal(vm.runInContext('bridgePreferTabFetch', context), false);
});

test('logged-in background fetch never touches tabs', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: MEMBER_PAGE, tabBody: GUEST_PAGE });
  const result = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/1/")', context);
  assert.equal(result.body, MEMBER_PAGE);
  assert.equal(calls.tab, 0);
});

test('background and tab retries share one fetch budget', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE });
  context.__now = 1000;
  vm.runInContext('Date.now = () => globalThis.__now', context);
  const budget = vm.runInContext('BRIDGE_FETCH_TIMEOUT_MS', context);
  const slowFetch = context.fetch;
  context.fetch = async (...args) => {
    context.__now += budget - 5000;
    return slowFetch(...args);
  };
  await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/61053/")', context);
  assert.equal(calls.tab, 1);
  assert.equal(calls.injected[0].args[2], 5000);

  // Too little left for the retry: keep the background answer.
  context.fetch = async (...args) => {
    context.__now += budget - 1000;
    return slowFetch(...args);
  };
  vm.runInContext('bridgePreferTabFetch = false', context);
  const kept = await vm.runInContext('bridgeFetchPreferringLogin("https://f95zone.to/threads/1/")', context);
  assert.equal(kept.body, GUEST_PAGE);
  assert.equal(calls.tab, 1);
});

test('tab fetch refuses non-allowlisted URLs without injecting', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: MEMBER_PAGE, tabBody: MEMBER_PAGE });
  const result = await vm.runInContext('bridgeFetchViaTab("https://example.com/")', context);
  assert.deepEqual({ ...result }, { ok: false, error: 'refused' });
  assert.equal(calls.tab, 0);
});

test('guest LC background fetch is repeated from an open LC tab, which is then preferred', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE });
  const queried = [];
  const query = context.chrome.tabs.query;
  context.chrome.tabs.query = async (filter) => { queried.push(filter.url); return query(filter); };
  const url = 'https://lewdcorner.com/threads/4321/';
  const first = await vm.runInContext(`bridgeFetchPreferringLogin(${JSON.stringify(url)}, "lc")`, context);
  assert.equal(first.body, MEMBER_PAGE);
  assert.equal(first.via, 'tab');
  assert.deepEqual(queried, ['https://lewdcorner.com/*']);
  assert.deepEqual([calls.background, calls.tab], [1, 1]);
  assert.equal(calls.injected[0].args[0], url);
  assert.equal(calls.injected[0].args[3], 'lewdcorner.com');
  assert.equal(vm.runInContext('bridgePreferLcTabFetch', context), true);
  // The LC preference does not change F95 fetches.
  assert.equal(vm.runInContext('bridgePreferTabFetch', context), false);

  const second = await vm.runInContext(`bridgeFetchPreferringLogin(${JSON.stringify(url)}, "lc")`, context);
  assert.equal(second.via, 'tab');
  assert.deepEqual([calls.background, calls.tab], [1, 2]);
});

test('guest LC background fetch is kept and marked when no LC tab is open', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE, tabs: [] });
  const result = await vm.runInContext('bridgeFetchPreferringLogin("https://lewdcorner.com/threads/1/", "lc")', context);
  assert.equal(result.body, GUEST_PAGE);
  assert.equal(result.via, 'background');
  assert.equal(result.tab_retry, 'no_tab');
  assert.equal(calls.tab, 0);
  assert.equal(vm.runInContext('bridgePreferLcTabFetch', context), false);
});

test('a failed LC tab fetch keeps the background answer and says so', async () => {
  const { context } = makeTabFetchHarness({ backgroundBody: GUEST_PAGE, tabBody: MEMBER_PAGE });
  context.chrome.scripting.executeScript = async () => [{ result: { ok: false, error: 'network' } }];
  const warnings = [];
  context.console = { ...console, warn: (...args) => warnings.push(args.join(' ')) };
  const result = await vm.runInContext('bridgeFetchPreferringLogin("https://lewdcorner.com/threads/1/", "lc")', context);
  assert.equal(result.body, GUEST_PAGE);
  assert.equal(result.tab_retry, 'failed');
  assert.ok(warnings.some((line) => line.includes('lc tab fetch failed: network')));
});

test('LC feed guest answers trigger the tab retry; member JSON does not', () => {
  const context = runBackground({ URL, TextEncoder, TextDecoder });
  const feed = 'https://lewdcorner.com/latest-updates/api?page=1&per_page=90&cat=Games&sort=date';
  const needs = (body) => vm.runInContext(`lcNeedsBrowserSession(${JSON.stringify(feed)}, ${JSON.stringify(body)})`, context);
  assert.equal(needs('<html>login</html>'), true);
  assert.equal(needs('﻿  <html>'), true);
  assert.equal(needs('"You must be logged in"'), true);
  assert.equal(needs('{"error":"not logged in"}'), true);
  assert.equal(needs('{"status":"ok","result":{}}'), false);
  assert.equal(needs('{"error":""}'), false);
  assert.equal(needs('not json'), false);
  const thread = (body) => vm.runInContext(`lcNeedsBrowserSession("https://lewdcorner.com/threads/1/", ${JSON.stringify(body)})`, context);
  assert.equal(thread(GUEST_PAGE), true);
  assert.equal(thread(MEMBER_PAGE), false);
});

test('LC tab fetch refuses F95 and other URLs without injecting', async () => {
  const { context, calls } = makeTabFetchHarness({ backgroundBody: MEMBER_PAGE, tabBody: MEMBER_PAGE });
  for (const url of ['https://f95zone.to/threads/1/', 'https://lewdcorner.com/members/1/', 'https://lewdcorner.com/threads/1/ ']) {
    const result = await vm.runInContext(`bridgeFetchViaTab(${JSON.stringify(url)}, 1000, "lc")`, context);
    assert.deepEqual({ ...result }, { ok: false, error: 'refused' });
  }
  const f95AsLc = await vm.runInContext('bridgeFetchViaTab("https://lewdcorner.com/threads/1/", 1000, "f95")', context);
  assert.deepEqual({ ...f95AsLc }, { ok: false, error: 'refused' });
  assert.equal(calls.tab, 0);
});

test('LC bridge fetch reports how the answer was made', async () => {
  const { context, sent, instances } = makeBridgeHarness({ canFetch: true });
  context.chrome.permissions.contains = async () => true;
  context.chrome.tabs = { query: async () => [] };
  context.chrome.scripting = { executeScript: async () => assert.fail('no tab to inject into') };
  context.fetch = async (url) => ({ status: 200, url, text: async () => GUEST_PAGE });
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  await vm.runInContext(
    'bridgeHandleFetchMessage({ id: "lc-1", url: "https://lewdcorner.com/threads/1/", site: "lc" })',
    context,
  );
  const result = JSON.parse(sent.at(-1));
  assert.equal(result.type, 'fetch_result');
  assert.equal(result.ok, true);
  assert.equal(result.via, 'background');
  assert.equal(result.tab_retry, 'no_tab');
});

test('injected tab fetch checks the redirect against the given host', async () => {
  const context = runBackground({ URL, TextEncoder, TextDecoder });
  context.content = { fetch: async (url) => ({ status: 200, url, text: async () => MEMBER_PAGE }) };
  const lc = await vm.runInContext('bridgeTabFetch("https://lewdcorner.com/threads/1/", 1024, 1000, "lewdcorner.com")', context);
  assert.equal(lc.ok, true);
  const wrongHost = await vm.runInContext('bridgeTabFetch("https://lewdcorner.com/threads/1/", 1024, 1000)', context);
  assert.deepEqual({ ...wrongHost }, { ok: false, error: 'offsite_redirect' });
});

test('injected tab fetch prefers the page fetch and guards redirects and size', async () => {
  const pageCalls = [];
  const context = runBackground({ URL, TextEncoder, TextDecoder });
  context.content = {
    fetch: async (url, init) => {
      pageCalls.push({ url, init });
      return { status: 200, url, text: async () => MEMBER_PAGE };
    },
  };
  const ok = await vm.runInContext('bridgeTabFetch("https://f95zone.to/threads/1/", 1024, 1000)', context);
  assert.equal(ok.ok, true);
  assert.equal(ok.body, MEMBER_PAGE);
  assert.equal(pageCalls[0].init.credentials, 'include');

  const small = await vm.runInContext('bridgeTabFetch("https://f95zone.to/threads/1/", 10, 1000)', context);
  assert.deepEqual({ ...small }, { ok: false, error: 'too_large' });

  context.content.fetch = async () => ({ status: 200, url: 'https://example.com/', text: async () => '' });
  const offsite = await vm.runInContext('bridgeTabFetch("https://f95zone.to/threads/1/", 1024, 1000)', context);
  assert.deepEqual({ ...offsite }, { ok: false, error: 'offsite_redirect' });
});

test('a later thread page never takes a reply image as the cover', () => {
  const firstPostImg = { src: 'https://attachments.f95zone.to/first.jpg', getAttribute: () => null };
  const run = (starter, pathname) => vm.runInNewContext(`${parser}\nextractGameInfo()`, {
    getThreadIdentity: () => ({ canonicalUrl: 'https://f95zone.to/threads/game.123/', id: '123' }),
    location: { pathname },
    document: {
      querySelector: (selector) => {
        if (selector === 'h1.p-title-value') {
          return { textContent: 'Game [Dev]', querySelectorAll: () => [], cloneNode() { return { textContent: 'Game [Dev]', querySelectorAll: () => [] }; } };
        }
        if (selector === '.message-threadStarterPost') return starter;
        if (selector === '.message-inner') return { querySelector: () => firstPostImg };
        return null;
      },
      querySelectorAll: () => [],
    },
    console,
  });
  assert.equal(run(null, '/threads/game.123/page-4').coverImage, '');
  assert.equal(run(null, '/threads/game.123/page-12').coverImage, '');
  assert.equal(run(null, '/threads/game.123/').coverImage, 'https://attachments.f95zone.to/first.jpg');
});

function makePairingContext(stored = {}) {
  const requests = [];
  const sockets = [];
  const context = {
    chrome: {
      runtime: { onMessage: { addListener: () => {} }, getManifest: () => ({ version: '1.2.8' }) },
      storage: {
        local: {
          get: async (key) => ({ [key]: stored[key] }),
          set: async (items) => { Object.assign(stored, items); },
        },
      },
    },
    crypto: require('node:crypto').webcrypto,
    TextEncoder,
    WebSocket: class { constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); } },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => ({ success: true }) }; },
    AbortController, setTimeout, clearTimeout, setInterval, clearInterval, console,
  };
  vm.createContext(context);
  vm.runInContext(backgroundSource, context);
  return { context, requests, sockets, stored };
}

test('every wLib request and the bridge socket carry one stored pairing token', async () => {
  const { context, requests, sockets, stored } = makePairingContext();
  await vm.runInContext('fetchWLibJson("http://localhost:8183/api/check?url=x")', context);
  await vm.runInContext('fetchWLibJson("http://localhost:8183/api/add", {method:"POST", headers:{"Content-Type":"application/json"}})', context);
  const token = stored.wlibPairingToken;
  assert.match(token, /^[0-9a-f]{64}$/);
  assert.equal(requests[0].options.headers['X-wLib-Extension'], token);
  assert.equal(requests[1].options.headers['X-wLib-Extension'], token);
  assert.equal(requests[1].options.headers['Content-Type'], 'application/json');
  vm.runInContext('ensureBridge()', context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(sockets.some((socket) => socket.url === `ws://localhost:8183/api/bridge?token=${token}`));
  assert.ok(sockets.every((socket) => socket.url.endsWith(token)));
});

test('a saved pairing token is reused and its code is stable', async () => {
  const saved = 'ab'.repeat(32);
  const { context, requests } = makePairingContext({ wlibPairingToken: saved });
  await vm.runInContext('fetchWLibJson("http://localhost:8183/api/check?url=x")', context);
  assert.equal(requests[0].options.headers['X-wLib-Extension'], saved);
  const expected = require('node:crypto').createHash('sha256').update(saved).digest('hex').toUpperCase();
  assert.equal(await vm.runInContext('pairingCode()', context), `${expected.slice(0, 4)}-${expected.slice(4, 8)}`);
});

test('a refused check tells the widget to ask for approval in wLib', () => {
  const { context } = makePairingContext();
  const payload = vm.runInContext('normalizeCheckPayload({exists:false, app_version:"9.9.9", error_code:"pairing_required"})', context);
  assert.match(payload.pairingNote, /Allow this browser extension/);
  assert.equal(vm.runInContext('normalizeCheckPayload({exists:false, app_version:"9.9.9"})', context).pairingNote, undefined);
});

test('LC bridge uses only allowlisted background fetches and its own permission', async () => {
  const { context, sent, instances } = makeBridgeHarness({ canFetch: true });
  let lcAllowed = false, webRequest = false;
  const requests = [];
  context.chrome.permissions.contains = async ({ origins, permissions }) => (
    permissions ? webRequest : origins[0] === 'https://f95zone.to/*' || lcAllowed
  );
  context.chrome.tabs = { query: async () => { assert.fail('LC should not use tabs'); } };
  context.fetch = async (url, options) => {
    requests.push({ url, options });
    return {
      status: 200,
      url: url.endsWith('/99/') ? 'https://f95zone.to/' : url,
      text: async () => 'response',
    };
  };
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  await vm.runInContext('bridgeSendHello()', context);
  assert.equal(JSON.parse(sent.at(-1)).can_fetch, true);
  assert.equal(JSON.parse(sent.at(-1)).can_fetch_lc, false);
  assert.equal(JSON.parse(sent.at(-1)).can_resolve_lc, false);
  assert.equal(JSON.parse(sent.at(-1)).can_fetch_lc_images, false);

  const fetchThroughBridge = async (url, site) => {
    await vm.runInContext(
      `bridgeHandleFetchMessage(${JSON.stringify({ id: 'lc-check', url, site })})`,
      context,
    );
    return JSON.parse(sent.at(-1));
  };
  const base = 'https://lewdcorner.com';
  const feed = `${base}/latest-updates/api?page=1&per_page=90&cat=Games&sort=date`;
  assert.equal((await fetchThroughBridge(feed, 'lc')).error, 'refused');
  lcAllowed = true;
  await vm.runInContext('bridgeSendStatus()', context);
  assert.deepEqual(JSON.parse(sent.at(-1)), { type: 'status', can_fetch: true, can_fetch_lc: true, can_resolve_lc: false, can_fetch_lc_images: true });
  webRequest = true;
  await vm.runInContext('bridgeSendStatus()', context);
  assert.equal(JSON.parse(sent.at(-1)).can_resolve_lc, true);
  webRequest = false;
  await vm.runInContext('bridgeSendStatus()', context);
  assert.equal(JSON.parse(sent.at(-1)).can_resolve_lc, false);

  assert.equal((await fetchThroughBridge(feed, 'lc')).body, 'response');
  assert.equal(requests[0].options.credentials, 'include');
  assert.equal((await fetchThroughBridge(`${base}/members/1/`, 'lc')).error, 'refused');
  assert.equal((await fetchThroughBridge(feed.replace('page=1', 'page=11'), 'lc')).error, 'refused');
  assert.equal((await fetchThroughBridge(`${base}/threads/1/`, 'f95')).error, 'refused');
  const redirected = await fetchThroughBridge(`${base}/threads/99/`, 'lc');
  assert.equal(redirected.error, 'offsite_redirect');
  assert.equal(redirected.body, undefined);
  assert.equal(requests.length, 2);
});


test('LC image fetch allows only attachment images and answers in chunks', async () => {
  const { context, sent, instances } = makeBridgeHarness({ canFetch: true });
  let lcAllowed = true;
  context.chrome.permissions.contains = async ({ origins, permissions }) => (permissions ? false : origins[0] === 'https://f95zone.to/*' || lcAllowed);
  context.chrome.tabs = { query: async () => { assert.fail('LC images must not use tabs'); } };
  context.btoa = btoa;
  context.Uint8Array = Uint8Array;
  const requests = [];
  let answer = () => ({});
  context.fetch = async (url, options) => {
    requests.push({ url, options });
    return answer(url);
  };
  const headers = values => ({ get: name => values[name.toLowerCase()] ?? null });
  const streamed = (parts, contentType = 'image/webp', extra = {}) => ({
    status: 200, url: requests.at(-1).url, headers: headers({ 'content-type': contentType, ...extra }),
    body: { getReader() { let i = 0; return { read: async () => (i < parts.length ? { done: false, value: parts[i++] } : { done: true }), cancel: async () => {}, releaseLock() {} }; } },
  });
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  await vm.runInContext('bridgeSendHello()', context);
  assert.equal(JSON.parse(sent.at(-1)).can_fetch_lc_images, true);

  const fetchImage = async url => {
    const before = sent.length;
    await vm.runInContext(`bridgeHandleFetchImage(${JSON.stringify({ id: 'img', url })})`, context);
    return sent.slice(before).map(raw => JSON.parse(raw));
  };
  const cover = 'https://lewdcorner.com/attachments/cover-webp.891912/';
  for (const url of [
    'https://lewdcorner.com/proxy.php?link=https%3A%2F%2Fexample.com%2F',
    'https://lewdcorner.com/masked/out?t=x',
    'https://lewdcorner.com/threads/1/',
    'https://lewdcorner.com/data/attachments/888/888308-c9.jpg?hash=a',
    'https://evil.test/attachments/cover-webp.1/',
    'https://lewdcorner.com/attachments/a b.1/',
    `${cover}?x=1`,
  ]) {
    assert.deepEqual(await fetchImage(url), [{ type: 'fetch_image_chunk', id: 'img', index: 0, last: true, ok: false, error: 'refused' }]);
  }
  assert.equal(requests.length, 0);

  answer = () => streamed([new Uint8Array([1, 2, 3])]);
  let messages = await fetchImage(cover);
  assert.equal(requests[0].options.credentials, 'include');
  assert.deepEqual(messages, [{ type: 'fetch_image_chunk', id: 'img', index: 0, ok: true, status: 200, content_type: 'image/webp', final_url: cover, data: 'AQID', last: true }]);

  answer = () => ({ ...streamed([new Uint8Array([1])]), url: 'https://cdn.example/x.webp' });
  messages = await fetchImage(cover);
  assert.deepEqual(messages.map(m => [m.ok, m.error, m.data]), [[false, 'offsite_redirect', undefined]]);
  answer = () => ({ ...streamed([new Uint8Array([1])]), url: 'https://lewdcorner.com/data/attachments/1/1.webp' });
  assert.equal((await fetchImage(cover))[0].ok, true);

  answer = () => ({ ...streamed([new TextEncoder().encode('<html>error</html>')], 'text/html; charset=utf-8'), status: 500 });
  messages = await fetchImage(cover);
  assert.deepEqual(messages, [{ type: 'fetch_image_chunk', id: 'img', index: 0, ok: true, status: 500, content_type: 'text/html; charset=utf-8', final_url: cover, data: '', last: true }]);

  const mib = 1024 * 1024;
  answer = () => streamed([new Uint8Array(10 * mib)]);
  messages = await fetchImage(cover);
  assert.deepEqual(messages.map(m => [m.index, m.last, Buffer.from(m.data, 'base64').length]), [[0, false, 4 * mib], [1, false, 4 * mib], [2, true, 2 * mib]]);
  assert.ok(messages.every(m => JSON.stringify(m).length < 8 * mib));
  assert.equal(messages[1].status, undefined);

  answer = () => streamed([new Uint8Array(15 * mib), new Uint8Array(6 * mib)]);
  assert.deepEqual((await fetchImage(cover)).map(m => [m.ok, m.error]), [[false, 'too_large']]);
  answer = () => streamed([new Uint8Array(1)], 'image/png', { 'content-length': String(21 * mib) });
  assert.deepEqual((await fetchImage(cover)).map(m => [m.ok, m.error]), [[false, 'too_large']]);

  lcAllowed = false;
  const count = requests.length;
  assert.equal((await fetchImage(cover))[0].error, 'refused');
  assert.equal(requests.length, count);
  await vm.runInContext('bridgeSendStatus()', context);
  assert.equal(JSON.parse(sent.at(-1)).can_fetch_lc_images, false);
});
