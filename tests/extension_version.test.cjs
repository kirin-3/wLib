// Runs as part of `npm run test:unit` in ui/ (and therefore CI).
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');
const parser = source.slice(source.indexOf('function extractGameInfo()'), source.indexOf('function getPlayStatusToneClass'));

const extractFromTitle = (title) => {
  const heading = { textContent: title, querySelector: () => null, querySelectorAll: () => [], cloneNode() { return this; } };
  const context = { getThreadIdentity: () => ({ canonicalUrl: '', id: 1 }), document: { querySelector: (selector) => selector === 'h1.p-title-value' ? heading : null, querySelectorAll: () => [] }, console };
  return vm.runInNewContext(`${parser}\nextractGameInfo()`, context);
};

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
