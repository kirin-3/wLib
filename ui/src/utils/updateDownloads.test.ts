// SPDX-License-Identifier: GPL-3.0-or-later
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {orderBuilds,preselect,settlePickerStart,downloadFailureMessage,needsExtensionSwitch,bulkDownloadDecision,pickerTargetVersion,platformLabel,selectableBuild,siteUnavailable,visibleArchivePassword} from './updateDownloads.ts';
import type {UpdateBuild} from '../services/api.ts';
const build = (platform: string, kind = 'full'): UpdateBuild => ({label:platform,section:'',platform,kind,mirrors:[{link_id:platform,host:'mega',label:'MEGA',part:'',split:false}]});
test('prefers native full builds and separates unsuitable platforms',()=>{
  const builds = [build('windows'),build('linux'),build('mac'),build('android')];
  assert.equal(preselect(builds,'linux')?.build.platform,'linux');
  assert.deepEqual(orderBuilds(builds,'linux').other.map(x=>x.platform),['mac','android']);
  assert.equal(preselect([build('linux','patch')],'linux'),null);
});
test('failure causes give a next step',()=>{
  for (const error of ['removed','quota','challenge','network error','file invalid','sign in','extension','permission']) assert.ok(downloadFailureMessage(error).length > error.length);
});
test('failure messages pick the most specific next step',()=>{
  assert.match(downloadFailureMessage('Enable Buzzheavier downloads in the wLib extension settings'),/Turn on Buzzheavier/);
  assert.match(downloadFailureMessage('unsupported host; open in browser'),/Open in browser/);
  assert.match(downloadFailureMessage('Open a browser with the wLib extension'),/Open a browser with the wLib extension and retry/);
});
test('the guide is offered for Buzzheavier and LewdCorner download switches', () => {
  assert.equal(needsExtensionSwitch('', 'permission'), true);
  assert.equal(needsExtensionSwitch('Permission denied'), true);
  assert.equal(needsExtensionSwitch('Enable Buzzheavier downloads in the wLib extension settings'), true);
  assert.equal(needsExtensionSwitch('Turn on LewdCorner downloads in the wLib extension settings'), true);
  assert.equal(needsExtensionSwitch('The file was removed', 'removed'), false);
  assert.equal(needsExtensionSwitch('Buzzheavier downloads quota reached'), false);
  assert.equal(needsExtensionSwitch('Sign in to LewdCorner to use LewdCorner downloads'), false);
});
test('bulk download starts the preselected build',()=>{
  const decision = bulkDownloadDecision({success:true,builds:[build('windows'),build('linux')]},'linux');
  assert.equal(decision.kind,'start');
  assert.equal(decision.kind === 'start' && decision.build.platform,'linux');
});
test('bulk download asks for a choice and names the reason',()=>{
  const reason = (links: Parameters<typeof bulkDownloadDecision>[0]) => {
    const decision = bulkDownloadDecision(links,'linux');
    return decision.kind === 'needs-choice' ? decision.reason : '';
  };
  assert.match(reason({success:true,builds:[build('linux','patch')]}),/Only patch builds/);
  assert.match(reason({success:true,builds:[]}),/No builds/);
  assert.match(reason({success:true,builds:[build('mac')]}),/No build for this platform/);
  const browserOnly: UpdateBuild = {...build('linux'),mirrors:[{link_id:'x',host:'gofile',label:'GoFile',part:'',split:false}]};
  assert.match(reason({success:true,builds:[browserOnly]}),/in-app mirror/);
  assert.match(reason({success:false,error:'Thread not found'}),/Links did not load: Thread not found/);
});

test('first installs can choose full builds but cannot select patches', () => {
  const patches = [build('linux', 'patch'), build('windows', 'patch')];
  assert.ok(selectableBuild(build('linux'), true));
  assert.ok(patches.every(patch => selectableBuild(patch, false)));
  assert.ok(patches.every(patch => !selectableBuild(patch, true)));
  assert.equal(preselect(patches, 'linux'), null);
});

test('only a first install falls back to the stored version for RPDL', () => {
  const game = { exe_path: '  ', version: ' 0.5 ', latest_version: '' };
  assert.equal(pickerTargetVersion(game), '0.5');
  assert.equal(pickerTargetVersion({ ...game, latest_version: ' 0.9 ' }), '0.9');
  assert.equal(pickerTargetVersion({ ...game, exe_path: 'game.exe' }), '');
  assert.equal(pickerTargetVersion({ ...game, exe_path: 'game.exe', latest_version: '0.9' }), '0.9');
});

test("LewdCorner failures explain the required browser action", () => {
  const check = "Finish the LewdCorner check in your browser";
  assert.equal(downloadFailureMessage(check), check);
  assert.equal(downloadFailureMessage("Resolving the link timed out", "lc"), check);
  assert.equal(downloadFailureMessage("lc_login_required"), "Sign in to LewdCorner in your browser");
  assert.equal(downloadFailureMessage("Sign in to LewdCorner in your browser"), "Sign in to LewdCorner in your browser");
  const permission = "Turn on LewdCorner downloads in the wLib extension settings";
  assert.equal(downloadFailureMessage(permission), permission);
  assert.equal(downloadFailureMessage("lc_plus_required"), "This is an LC Plus thread. Use F95Zone or an LC Plus account.");
});

test('a site the game no longer offers falls back to the default', () => {
  const f95 = [{site: 'f95' as const, version: '1.0', older: false}];
  assert.equal(siteUnavailable('lc', f95), true);
  assert.equal(siteUnavailable('f95', f95), false);
  assert.equal(siteUnavailable('', f95), false);
  assert.equal(siteUnavailable('lc', []), false);
  assert.equal(siteUnavailable('lc', undefined), false);
});

test('old builds are kept apart and never preselected', () => {
  const old = {...build('windows', 'old'), section: 'Old Versions'};
  const builds = [old, build('windows')];
  const ordered = orderBuilds(builds, 'linux');
  assert.deepEqual(ordered.old, [old]);
  assert.ok(!ordered.suitable.includes(old) && !ordered.other.includes(old));
  assert.equal(preselect(builds, 'linux')?.build.kind, 'full');
  assert.equal(preselect([old], 'linux'), null);
});

test('bulk download asks when only older versions were found', () => {
  const decision = bulkDownloadDecision({success: true, builds: [build('windows', 'old'), build('mac')]}, 'linux');
  assert.equal(decision.kind, 'needs-choice');
  assert.equal(decision.kind === 'needs-choice' && decision.reason, 'Only older versions were found');
});

test('ios and android (APK) builds sit under other platforms and are never preselected', () => {
  const apk: UpdateBuild = {...build('android'), label: 'APK'};
  const ios: UpdateBuild = {...build('ios'), label: 'iOS (IPA)'};
  for (const host of ['linux', 'windows'] as const) {
    assert.deepEqual(orderBuilds([build('windows/linux'), apk, ios], host).other, [apk, ios]);
    assert.equal(preselect([apk, ios], host), null);
  }
  assert.equal(platformLabel('ios'), 'iOS');
  assert.equal(platformLabel('android'), 'Android');
  assert.equal(platformLabel('windows/linux'), 'Windows / Linux');
  assert.equal(platformLabel('unknown'), 'Any platform');
  assert.equal(platformLabel('all'), 'All platforms');
});

test('archive password visibility trims whitespace and hides empty values', () => {
  assert.equal(visibleArchivePassword('f95zone'), 'f95zone');
  assert.equal(visibleArchivePassword('  my pass  '), 'my pass');
  assert.equal(visibleArchivePassword(''), '');
  assert.equal(visibleArchivePassword('   '), '');
  assert.equal(visibleArchivePassword(undefined), '');
});

test('a late download answer only changes the picker that started it',async()=>{
  const run = async (response: {success: boolean; error?: string}, stillOpen: boolean) => {
    const events: string[] = [];
    let release: (value: {success: boolean; error?: string}) => void = () => {};
    const request = new Promise<{success: boolean; error?: string}>(resolve => { release = resolve; });
    const settled = settlePickerStart(request, {
      stillOpen: () => stillOpen,
      refresh: () => { events.push('refresh'); },
      close: () => { events.push('close'); },
      notify: () => { events.push('notify'); },
      showError: message => { events.push(`error:${message}`); },
    });
    release(response);
    await settled;
    return events;
  };
  // The picker that started the download is still open.
  assert.deepEqual(await run({success: true}, true), ['refresh','close','notify']);
  assert.deepEqual(await run({success: false, error: 'Already queued'}, true), ['error:Already queued']);
  assert.deepEqual(await run({success: false}, true), ['error:Could not start download']);
  // Game A's picker closed and game B's opened before A's answer came.
  assert.deepEqual(await run({success: true}, false), ['refresh','notify']);
  assert.deepEqual(await run({success: false, error: 'Already queued'}, false), []);
  const thrown = await (async () => {
    const events: string[] = [];
    await settlePickerStart(Promise.reject(new Error('Bridge gone')), {
      stillOpen: () => false, refresh() {}, close() {}, notify() {}, showError: message => { events.push(message); },
    });
    return events;
  })();
  assert.deepEqual(thrown, []);
});
