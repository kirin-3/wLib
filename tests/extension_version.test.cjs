// Runs as part of `npm run test:unit` in ui/ (and therefore CI).
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');
const backgroundSource = readFileSync(path.join(__dirname, '..', 'extension', 'background.js'), 'utf8');
const parser = source.slice(source.indexOf('function extractGameInfo()'), source.indexOf('function getPlayStatusToneClass'));

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
        json: async () => ({ success: false, error: 'Already saved', error_code: 'duplicate_url' }),
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

const runThreadCheck = (runtime) => {
  const context = { console, chrome: { runtime } };
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
  });
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
  });
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
  assert.equal(manifest.version, '1.2.0');
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
  // The F95 fetch bridge must not touch tabs; optional-site registration may
  // inject into already-open pages after an explicit permission grant.
  assert.ok(!backgroundSource.slice(backgroundSource.indexOf('// ---- Extension fetch fallback'), backgroundSource.indexOf('// Optional sites')).includes('chrome.tabs'));
  assert.ok(!backgroundSource.includes('chrome.windows'));
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

const makeBridgeHarness = ({ canFetch = true } = {}) => {
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
  };
  const context = {
    chrome: chromeStub,
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
  return { context, sent, instances, timeouts, intervals };
};

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
  assert.equal(hello.protocol, 1);
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
