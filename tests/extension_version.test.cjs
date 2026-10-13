// Runs as part of `npm run test:unit` in ui/ (and therefore CI).
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');
const backgroundSource = readFileSync(path.join(__dirname, '..', 'extension', 'background.js'), 'utf8');
const parser = source.slice(source.indexOf('function extractGameInfo()'), source.indexOf('function getPlayStatusToneClass'));

const extractFromTitle = (title, labels = []) => {
  const heading = {
    textContent: title,
    querySelectorAll: (selector) => selector === 'a.labelLink'
      ? labels.map(textContent => ({ textContent })) : [],
    cloneNode() { return { textContent: title, querySelectorAll: () => [] }; },
  };
  const context = { getThreadIdentity: () => ({ canonicalUrl: '', id: 1 }), document: { querySelector: (selector) => selector === 'h1.p-title-value' ? heading : null, querySelectorAll: () => [] }, console };
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
