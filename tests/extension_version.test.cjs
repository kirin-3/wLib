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
    vm.runInContext('fetchWithTimeout("http://localhost:8183/api/check?url=x", undefined, 20)', context),
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
