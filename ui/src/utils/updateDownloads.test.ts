// SPDX-License-Identifier: GPL-3.0-or-later
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {orderBuilds,preselect,downloadFailureMessage,bulkDownloadDecision} from './updateDownloads.ts';
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
