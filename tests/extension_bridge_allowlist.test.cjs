// Runs as part of `npm run test:unit` in ui/ (and therefore CI).
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const backgroundSource = readFileSync(path.join(__dirname, '..', 'extension', 'background.js'), 'utf8');

const makeContext = () => {
  const sent = [];
  const instances = [];
  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      instances.push(this);
    }
    send(data) { sent.push(data); }
    close() { this.readyState = 3; }
  }
  const fetchCalls = [];
  const context = {
    chrome: {
      runtime: {
        onMessage: { addListener: () => {} },
        getManifest: () => ({ version: '1.2.12' }),
        onStartup: { addListener: () => {} },
        onInstalled: { addListener: () => {} },
      },
      alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
      permissions: {
        contains: async () => true,
        onAdded: { addListener: () => {} },
        onRemoved: { addListener: () => {} },
      },
      storage: { local: { set: async () => {} }, onChanged: { addListener: () => {} } },
    },
    WebSocket: FakeWebSocket,
    fetch: async (url) => {
      fetchCalls.push(url);
      return { status: 200, url, text: async () => '<html>ok</html>' };
    },
    console,
    AbortController,
    URL,
    TextEncoder,
    TextDecoder,
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
  };
  vm.createContext(context);
  vm.runInContext(backgroundSource, context);
  vm.runInContext('bridgeToken = ""', context);
  return { context, sent, instances, fetchCalls };
};

const WATCHED = 'https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page=';
const BOOKMARKS = 'https://f95zone.to/watched/threads?tab=bookmarks&bm=games&sort=title&page=';

test('bridge allowlist accepts the two list URL forms on pages 1-200', () => {
  const { context } = makeContext();
  const allowed = (url) => vm.runInContext(`isBridgeUrlAllowed(${JSON.stringify(url)})`, context);
  for (const page of ['1', '2', '9', '10', '99', '150', '200']) {
    assert.equal(allowed(WATCHED + page), true, `watched page ${page}`);
    assert.equal(allowed(BOOKMARKS + page), true, `bookmarks page ${page}`);
  }
  assert.equal(allowed('https://f95zone.to/threads/12345/'), true);
  assert.equal(allowed('https://f95zone.to/sam/checker.php?threads=1,2'), true);
});

test('bridge allowlist refuses other watched views and malformed list URLs', () => {
  const { context } = makeContext();
  const allowed = (url) => vm.runInContext(`isBridgeUrlAllowed(${JSON.stringify(url)})`, context);
  const refused = [
    'https://f95zone.to/watched/threads?tab=other&unread=0&page=1',
    'https://f95zone.to/watched/threads?tab=other&unread=0&sort=title&page=1',
    'https://f95zone.to/watched/threads?tab=new&unread=0&sort=title&page=1',
    'https://f95zone.to/watched/threads?tab=bookmarks&bm=posts&sort=title&page=1',
    `${WATCHED}1&extra=1`,
    `${WATCHED}1#top`,
    'https://f95zone.to/watched/threads?unread=0&tab=games&sort=title&page=1',
    'https://f95zone.to/watched/threads?tab=games&sort=title&unread=0&page=1',
    'https://f95zone.to/watched/threads?bm=games&tab=bookmarks&sort=title&page=1',
    'https://f95zone.to/watched/threads?tab=games&unread=0&sort=version&page=1',
    'https://f95zone.to/watched/threads?tab=games&unread=0&page=1',
    `${WATCHED}0`,
    `${WATCHED}201`,
    `${WATCHED}999`,
    `${WATCHED}1000`,
    `${WATCHED}01`,
    `${BOOKMARKS}0`,
    `${BOOKMARKS}201`,
    `${BOOKMARKS}01`,
    `${WATCHED}`,
    `${WATCHED}-1`,
    `http://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page=1`,
    `https://f95zone.to.evil.example/watched/threads?tab=games&unread=0&sort=title&page=1`,
    'https://example.com/',
  ];
  for (const url of refused) {
    assert.equal(allowed(url), false, url);
  }
});

test('bridge fetch refuses a list URL with a bad page without a network request', async () => {
  const { context, fetchCalls } = makeContext();
  const result = await vm.runInContext(`bridgeFetchUrl(${JSON.stringify(`${WATCHED}201`)})`, context);
  assert.equal(result.ok, false);
  assert.equal(result.error, 'refused');
  assert.equal(fetchCalls.length, 0);
  const ok = await vm.runInContext(`bridgeFetchUrl(${JSON.stringify(`${BOOKMARKS}3`)})`, context);
  assert.equal(ok.ok, true);
  assert.deepEqual(fetchCalls, [`${BOOKMARKS}3`]);
});

test('bridge hello reports list-fetch support', async () => {
  const { context, sent, instances } = makeContext();
  vm.runInContext('ensureBridge()', context);
  instances[0].readyState = 1;
  instances[0].onopen();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const hello = sent.map((raw) => JSON.parse(raw)).find((msg) => msg.type === 'hello');
  assert.ok(hello);
  assert.equal(hello.can_fetch_lists, true);
  assert.equal(hello.can_fetch, true);
  assert.equal(hello.extension_version, '1.2.12');
});
