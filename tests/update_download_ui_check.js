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
      await picker.getByRole('button',{name:'Gofile',exact:true}).click();
      check(await picker.getByRole('button',{name:'Download',exact:true}).count()===0,`${theme}: unsupported host is browser only`);
      await picker.getByRole('button',{name:'Close',exact:true}).click();
      await detail.getByRole('button',{name:'Close',exact:true}).click();
      await page.getByRole('link',{name:/Updates/}).click();
      const button = page.getByRole('button',{name:'Download update',exact:true});
      await button.click();
      picker = page.getByRole('dialog',{name:'Download update'});
      await picker.getByText('Windows/Linux').waitFor();
      await picker.getByRole('button',{name:'Close',exact:true}).click();
      await page.evaluate(()=>{window.pywebview={api:{get_running_games:async()=>[71]}};});
      await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent.trim()==='Download update' && button.disabled), undefined, {timeout:8000}).catch(()=>{throw new Error(`${theme}: running state did not update`);});
      check(await button.isDisabled(),`${theme}: running game disables update download`);
    }
    // Exercise job polling and the existing archive-install handoff with mock responses.
    await page.evaluate(() => {
      localStorage.setItem('wlib-mock-games',JSON.stringify([{id:71,title:'Download fixture',exe_path:'/fixture/game.sh',version:'1.0',latest_version:'2.0',f95_url:'https://f95zone.to/threads/71/'}]));
    });
    await page.goto(new URL('/#/updates',previousUrl).toString());
    await page.reload();
    await page.evaluate(()=>{
      window.testDownloadJob = null;
      window.pywebview={api:{
        get_running_games:async()=>[],
        get_update_download_status:async()=>({success:true,jobs:window.testDownloadJob ? [window.testDownloadJob] : []}),
        start_update_download:async gameId=>{
          window.testDownloadJob={job_id:'fixture',game_id:gameId,stage:'downloading',bytes:1048576,total:2097152,speed:100,error:'',archive_path:''};
          return {success:true,job_id:'fixture'};
        },
        inspect_archive:async()=>({success:true,downloaded_by_wlib:true,title:'Download fixture',version:'2.0',library_folder:'/games',destination:'/games/game',removable:true}),
      }};
    });
    await page.getByRole('button',{name:'Download update',exact:true}).click();
    const picker = page.getByRole('dialog',{name:'Download update'});
    await picker.getByRole('button',{name:'Download',exact:true}).click();
    await picker.getByText(/Downloading 1.0 MiB/).waitFor();
    await picker.getByRole('button',{name:'Close',exact:true}).click();
    await page.evaluate(()=>{window.testDownloadJob.stage='done';window.testDownloadJob.archive_path='/tmp/update.zip';});
    const install = page.getByRole('dialog').filter({hasText:'Delete archive after install'});
    await install.getByText('Delete archive after install').waitFor();
    await page.waitForFunction(()=>[...document.querySelectorAll('label')].some(label=>label.textContent.includes('Delete archive after install') && label.querySelector('input')?.checked),undefined,{timeout:8000}).catch(async()=>{throw new Error('Delete default missing: '+await install.innerText());});
    check(errors.length===0,errors.join('\n'));
    return 'Download picker entry points, browser fallback and running state pass in light and dark themes.';
  } finally {
    await page.evaluate(items=>{delete window.pywebview; for (const [key,value] of items) value===null ? localStorage.removeItem(key) : localStorage.setItem(key,value);},previous);
    await page.goto(previousUrl);
  }
}
