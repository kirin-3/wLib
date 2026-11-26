// SPDX-License-Identifier: GPL-3.0-or-later
async page => {
  const check = (value,message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ['wlib-mock-games','wlib-theme','wlib-mock-platform','wlib-ui-motion','wlib-mock-rpdl'];
  const previous = await page.evaluate(keys=>keys.map(key=>[key,localStorage.getItem(key)]),keys);
  const errors = [];
  page.on('pageerror',error=>errors.push(String(error)));
  try {
    for (const theme of ['light','dark']) {
      await page.evaluate(theme=>{
        delete window.pywebview;
        localStorage.setItem('wlib-theme',theme);
        localStorage.setItem('wlib-mock-platform','linux');
        localStorage.setItem('wlib-ui-motion','off');
        localStorage.setItem('wlib-mock-games',JSON.stringify([{id:71,title:'Download fixture',exe_path:'/fixture/game.sh',version:'1.0',latest_version:'2.0',f95_url:'https://f95zone.to/threads/71/'}]));
      },theme);
      await page.goto(new URL('/#/',previousUrl).toString());
      await page.reload();
      await page.getByRole('row', { name: 'Download fixture'}).press('Enter');
      const detail = page.getByRole('dialog').filter({has:page.getByRole('button',{name:'Download update',exact:true})});
      await detail.getByRole('button',{name:'Download update',exact:true}).click();
      let picker = page.getByRole('dialog',{name:'Download update'});
      await picker.getByText('Windows/Linux').waitFor();
      check(await picker.getByRole('button',{name:'Download',exact:true}).isVisible(),`${theme}: preferred mirror selected`);
      check(/^Installed\s*1\.0\s*→\s*(to\s+)?\S+/.test((await picker.getByTestId('picker-version').innerText()).trim()),`${theme}: header names the installed and new version`);
      await picker.getByRole('radio',{name:/^Gofile/}).click();
      check(await picker.getByRole('button',{name:'Download',exact:true}).count()===0,`${theme}: unsupported host is browser only`);
      await picker.getByRole('button',{name:'Close',exact:true}).first().click();
      await detail.getByRole('button',{name:'Close',exact:true}).click();
      await page.getByRole('link',{name:/Updates/}).click();
      const button = page.getByRole('button',{name:'Download update',exact:true});
      await button.click();
      picker = page.getByRole('dialog',{name:'Download update'});
      await picker.getByText('Windows/Linux').waitFor();
      await picker.getByRole('button',{name:'Close',exact:true}).first().click();
      await page.evaluate(()=>{window.pywebview={api:{get_running_games:async()=>[71]}};});
      await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent.trim()==='Download update' && (button.disabled || button.getAttribute('aria-disabled')==='true')), undefined, {timeout:15000}).catch(()=>{throw new Error(`${theme}: running state did not update`);});
      check(await button.isDisabled(),`${theme}: running game disables update download`);
    }
    // RPDL signed out: the torrents are information only, the mirror choice
    // stays, and a button opens the RPDL sign-in row in Settings.
    await page.evaluate(()=>{
      delete window.pywebview;
      localStorage.removeItem('wlib-mock-rpdl');
      localStorage.setItem('wlib-mock-games',JSON.stringify([{id:71,title:'Download fixture',exe_path:'/fixture/game.sh',version:'1.0',latest_version:'2.0',f95_url:'https://f95zone.to/threads/71/'}]));
    });
    await page.goto(new URL('/#/updates',previousUrl).toString());
    await page.reload();
    await page.getByRole('button',{name:'Download update',exact:true}).click();
    let rpdlPicker = page.getByRole('dialog',{name:'Download update'});
    const rpdlSection = rpdlPicker.getByTestId('rpdl-section');
    // A mirror works, so the RPDL section stays closed; its summary says why it is not usable.
    await rpdlSection.getByTestId('rpdl-summary-state').filter({hasText:'Sign in needed'}).waitFor({timeout:8000});
    check(!(await rpdlSection.evaluate(element=>element.open)),'signed out with a working mirror: RPDL starts closed');
    check(/RPDL torrents \(2\)/.test(await rpdlSection.locator('summary').innerText()),'summary gives the torrent count');
    await rpdlSection.locator('summary').click();
    await rpdlSection.getByTestId('rpdl-sign-in').waitFor({timeout:8000});
    const torrents = rpdlSection.locator('button.torrent');
    check(await torrents.count()>0,'signed out: torrents still listed');
    for (const torrent of await torrents.all()) check(await torrent.isDisabled(),'signed out: torrent rows are disabled');
    await torrents.first().click({force:true});
    check(await rpdlPicker.getByRole('button',{name:'Download',exact:true}).isEnabled(),'signed out: preselected mirror keeps Download');
    check(await rpdlPicker.locator('button.mirror.selected').count()===1,'signed out: the mirror stays selected');
    check(await rpdlPicker.getByTestId('rpdl-export-actions').count()===0,'signed out: no torrent export actions');
    await rpdlSection.getByRole('button',{name:'Open RPDL settings'}).click();
    await page.waitForURL(/#\/settings#file-hosts-rpdl/);
    check(await page.getByRole('dialog',{name:'Download update'}).count()===0,'Open RPDL settings closes the picker');
    await page.locator('#file-hosts-rpdl').waitFor({timeout:8000});
    // Signed in: the clear match is preselected and every torrent action shows.
    await page.evaluate(()=>{localStorage.setItem('wlib-mock-rpdl','fixture');});
    await page.goto(new URL('/#/updates',previousUrl).toString());
    await page.reload();
    await page.getByRole('button',{name:'Download update',exact:true}).click();
    rpdlPicker = page.getByRole('dialog',{name:'Download update'});
    await rpdlPicker.getByTestId('rpdl-export-actions').waitFor({timeout:8000});
    check(await rpdlPicker.getByTestId('rpdl-section').evaluate(element=>element.open),'signed in: the preselected torrent opens the RPDL section');
    check(await rpdlPicker.getByTestId('rpdl-sign-in').count()===0,'signed in: no sign-in note');
    check(await rpdlPicker.locator('button.torrent.selected').count()===1,'signed in: clear match preselected');
    check(await rpdlPicker.getByRole('button',{name:'Download',exact:true}).isEnabled(),'signed in: torrent Download enabled');
    check(await rpdlPicker.getByRole('button',{name:'Copy magnet'}).isVisible(),'signed in: Copy magnet shows');
    await rpdlPicker.locator('button.mirror:not(.torrent):not(.site-option)').first().click();
    check(await rpdlPicker.locator('button.torrent.selected').count()===0,'signed in: picking a mirror clears the torrent');
    check(await rpdlPicker.getByTestId('rpdl-section').evaluate(element=>element.open),'the RPDL section does not close by itself');
    await rpdlPicker.locator('button.torrent').first().click();
    check(await rpdlPicker.locator('button.mirror.selected:not(.torrent)').count()===0,'signed in: picking a torrent clears the mirror');
    check(await rpdlPicker.getByTestId('rpdl-export-actions').isVisible(),'signed in: export actions after picking a torrent');
    await rpdlPicker.getByRole('button',{name:'Close',exact:true}).first().click();
    await page.evaluate(()=>{localStorage.removeItem('wlib-mock-rpdl');});
    // No in-app mirror for this computer: RPDL is the way, so it opens by itself, also signed out.
    await page.evaluate(()=>{window.pywebview={api:{get_update_download_links:async()=>({success:true,site:'f95',sites:[{site:'f95',version:'2.0',older:false}],version:'2.0',archive_password:'',
      builds:[{label:'Windows/Linux',section:'',platform:'windows/linux',kind:'full',mirrors:[{link_id:'only-gofile',host:'gofile',label:'Gofile',part:'',split:false}]}]})}};});
    await page.getByRole('button',{name:'Download update',exact:true}).click();
    rpdlPicker = page.getByRole('dialog',{name:'Download update'});
    await rpdlPicker.getByTestId('rpdl-sign-in').waitFor({timeout:8000});
    check(await rpdlPicker.getByTestId('rpdl-section').evaluate(element=>element.open),'no in-app mirror: RPDL opens by itself');
    check(/Installed\s*1\.0\s*→\s*(to\s+)?2\.0/.test(await rpdlPicker.getByTestId('picker-version').innerText()),'header uses the thread version');
    await rpdlPicker.getByRole('button',{name:'Close',exact:true}).first().click();
    await page.evaluate(()=>{delete window.pywebview;});
    // A wishlisted game: no installed version, and a signed-in clear match opens RPDL.
    await page.evaluate(()=>{
      localStorage.setItem('wlib-mock-rpdl','fixture');
      localStorage.setItem('wlib-mock-games',JSON.stringify([{id:72,title:'Wishlisted fixture',exe_path:'',version:'0.7',latest_version:'0.8',f95_url:'https://f95zone.to/threads/72/'}]));
    });
    await page.goto(new URL('/#/',previousUrl).toString());
    await page.reload();
    await page.getByText('Wishlisted fixture').first().click();
    await page.getByRole('button',{name:'Download game',exact:true}).first().click();
    const wishPicker = page.getByRole('dialog',{name:'Download game'});
    await wishPicker.getByTestId('rpdl-export-actions').waitFor({timeout:8000});
    check(await wishPicker.getByTestId('rpdl-section').evaluate(element=>element.open),'wishlisted: the matching torrent opens RPDL');
    check(/^Version\s+\S+$/.test((await wishPicker.getByTestId('picker-version').innerText()).trim()),'wishlisted: header names only the version to download');
    await wishPicker.getByRole('button',{name:'Close',exact:true}).first().click();
    await page.evaluate(()=>{localStorage.removeItem('wlib-mock-rpdl');});
    // The new flow: the picker adds the download and closes, the row shows on
    // the Downloads page, and a finished download opens the install dialog.
    await page.evaluate(() => {
      localStorage.setItem('wlib-mock-games',JSON.stringify([{id:71,title:'Download fixture',exe_path:'/fixture/game.sh',version:'1.0',latest_version:'2.0',f95_url:'https://f95zone.to/threads/71/'}]));
    });
    await page.goto(new URL('/#/updates',previousUrl).toString());
    await page.reload();
    await page.evaluate(()=>{
      window.testDownloadJob = null;
      const summary = {max_active:2,active:0,hold:'',hold_reason:'',hold_override:false,hold_keeps_running:[],speed:0,peak:0,history:[],
        forecast:{needed:0,free:null,unknown:0,fits:true,missing:0},
        settings:{download_limit_kib:0,download_max_active:2,download_pause_while_playing:true,download_pause_on_battery:false}};
      window.pywebview={api:{
        get_running_games:async()=>[],
        get_download_queue:async()=>({success:true,jobs:window.testDownloadJob ? [window.testDownloadJob] : [],queue:summary,seeding:[]}),
        find_download_offers:async()=>({success:true,offers:[]}),
        start_update_download:async(gameId,linkId,replaces)=>{
          window.testDownloadJob={job_id:'fixture',kind:'host',game_id:gameId,title:'Download fixture',version:'2.0',state:'running',reason:'',position:0,
            error_code:'',error_text:'',bytes_done:1048576,bytes_total:2097152,speed:100,eta:10,retries:0,retry_at:null,retry_in:null,
            created_at:1,finished_at:null,can_continue:true,source:{host:'pixeldrain',label:'Windows/Linux'},details:{stage:'downloading'},replaces};
          return {success:true,job_id:'fixture'};
        },
        inspect_archive:async()=>({success:true,downloaded_by_wlib:true,title:'Download fixture',version:'2.0',library_folder:'/games',destination:'/games/game',removable:true}),
      }};
    });
    await page.getByRole('button',{name:'Download update',exact:true}).click();
    const picker = page.getByRole('dialog',{name:'Download update'});
    await picker.getByRole('button',{name:'Download',exact:true}).click();
    await picker.waitFor({state:'detached',timeout:8000}).catch(()=>{throw new Error('The picker did not close after the download was added');});
    const toast = page.getByRole('button',{name:'Open Downloads'});
    await toast.first().waitFor({timeout:8000});
    await toast.first().click();
    await page.waitForURL(/#\/downloads/);
    const row = page.locator('[data-testid="download-row"]').filter({hasText:'Download fixture'});
    await row.getByText('Downloading 50%').waitFor({timeout:8000});
    check(await page.getByText(/Downloading · 1 · /).isVisible(),'The row is under "Downloading"');
    await page.evaluate(()=>{Object.assign(window.testDownloadJob,{state:'ready',bytes_done:2097152,speed:0,finished_at:2,details:{stage:'done',archive_path:'/tmp/update.zip'}});});
    const install = page.getByRole('dialog').filter({hasText:'Delete archive after install'});
    await install.getByText('Delete archive after install').waitFor({timeout:8000});
    await page.waitForFunction(()=>[...document.querySelectorAll('label')].some(label=>label.textContent.includes('Delete archive after install') && label.querySelector('input')?.checked),undefined,{timeout:8000}).catch(async()=>{throw new Error('Delete default missing: '+await install.innerText());});
    check(errors.length===0,errors.join('\n'));
    return 'Picker entry points, browser fallback, running state, RPDL signed out and signed in, when the RPDL section opens, the version header, closing after add, the Downloads row and the install offer pass in light and dark themes.';
  } finally {
    await page.evaluate(items=>{delete window.pywebview; for (const [key,value] of items) value===null ? localStorage.removeItem(key) : localStorage.setItem(key,value);},previous);
    await page.goto(previousUrl);
  }
}
