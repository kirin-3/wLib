// SPDX-License-Identifier: GPL-3.0-or-later
async page => {
  const check = (value,message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ['wlib-mock-games','wlib-theme','wlib-mock-platform','wlib-ui-motion'];
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
      await page.getByRole('checkbox', { name: 'Download fixture. Press Enter for details.'}).press('Enter');
      const detail = page.getByRole('dialog').filter({has:page.getByRole('button',{name:'Download update',exact:true})});
      await detail.getByRole('button',{name:'Download update',exact:true}).click();
      let picker = page.getByRole('dialog',{name:'Download update'});
      await picker.getByText('Windows/Linux').waitFor();
      check(await picker.getByRole('button',{name:'Download',exact:true}).isVisible(),`${theme}: preferred mirror selected`);
      await picker.getByRole('button',{name:/^Gofile/}).click();
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
      await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent.trim()==='Download update' && button.disabled), undefined, {timeout:15000}).catch(()=>{throw new Error(`${theme}: running state did not update`);});
      check(await button.isDisabled(),`${theme}: running game disables update download`);
    }
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
    return 'Picker entry points, browser fallback, running state, closing after add, the Downloads row and the install offer pass in light and dark themes.';
  } finally {
    await page.evaluate(items=>{delete window.pywebview; for (const [key,value] of items) value===null ? localStorage.removeItem(key) : localStorage.setItem(key,value);},previous);
    await page.goto(previousUrl);
  }
}
