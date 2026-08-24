(() => {
  const FLOW_PAIR_AUTO_VERSION = '14.7.4';
  if (window.__FLOW_PAIR_AUTO_VERSION__ === FLOW_PAIR_AUTO_VERSION && window.FlowPairAuto) return;
  window.__FLOW_PAIR_AUTO_VERSION__ = FLOW_PAIR_AUTO_VERSION;
  // Deliberately ignore legacy __FLOW_PAIR_AUTO_V1__. Older extension versions left it behind.

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = v => String(v ?? '').replace(/\s+/g,' ').trim().toLowerCase();
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  };
  const waitFor = async (fn, timeout=8000, step=100) => {
    const t=Date.now();
    while(Date.now()-t<timeout){ const x=fn(); if(x) return x; await sleep(step); }
    return null;
  };
  const propsOf = el => {
    if(!el) return null;
    const k=Object.keys(el).find(k=>k.startsWith('__reactProps$'));
    return k?el[k]:null;
  };
  const evt = (target,current,type) => {
    let defaultPrevented=false;
    let propagationStopped=false;
    return {
      type,target,currentTarget:current,
      nativeEvent:{
        type,target,currentTarget:current,button:0,
        buttons:type.includes('down')?1:0,
        pointerType:'mouse',isPrimary:true,
        ctrlKey:false,shiftKey:false,altKey:false,metaKey:false
      },
      button:0,buttons:type.includes('down')?1:0,
      pointerType:'mouse',isPrimary:true,detail:1,
      ctrlKey:false,shiftKey:false,altKey:false,metaKey:false,
      defaultPrevented:false,
      preventDefault(){defaultPrevented=true;this.defaultPrevented=true;},
      isDefaultPrevented(){return defaultPrevented;},
      stopPropagation(){propagationStopped=true;},
      isPropagationStopped(){return propagationStopped;},
      persist(){}
    };
  };
  const invoke = (target,names,max=8) => {
    let cur=target;
    for(let i=0;cur&&i<=max;i++,cur=cur.parentElement){
      const p=propsOf(cur); if(!p) continue;
      for(const n of names){
        if(typeof p[n] !== 'function') continue;
        const type=n==='onPointerDown'?'pointerdown':n==='onMouseDown'?'mousedown':'click';
        p[n](evt(target,cur,type));
        return {handlerName:n,element:cur};
      }
    }
    return null;
  };

  // SETTINGS
  let lastSettingsTrigger = null;

  const getLinkedSettingsMenu = trigger => {
    if(!trigger) return null;

    const controls=trigger.getAttribute('aria-controls');
    if(controls){
      const linked=document.getElementById(controls);
      if(linked && visible(linked) && (
        linked.getAttribute('data-state')==='open' ||
        linked.getAttribute('role')==='menu' ||
        linked.querySelector?.('button[role="tab"]')
      )) return linked;
    }

    if(trigger.id){
      const byLabel=[...document.querySelectorAll('[role="menu"][data-state="open"]')]
        .filter(visible)
        .find(menu=>menu.getAttribute('aria-labelledby')===trigger.id);
      if(byLabel) return byLabel;
    }

    return null;
  };

  const looksLikeMainSettingsMenu = menu => {
    if(!menu || !visible(menu)) return false;

    const tabs=[...menu.querySelectorAll('button[role="tab"]')];
    const controls=tabs.map(b=>b.getAttribute('aria-controls')||'');

    if(
      controls.some(v=>v.endsWith('-content-IMAGE')) &&
      controls.some(v=>v.endsWith('-content-VIDEO'))
    ) return true;

    const t=norm(menu.textContent);
    return (
      (
        t.includes('ingredients') ||
        t.includes('frames') ||
        (t.includes('h?nh ?nh') && t.includes('video')) ||
        (t.includes('image') && t.includes('video'))
      ) &&
      (t.includes('16:9') || t.includes('9:16')) &&
      /x[1-4]/.test(t)
    );
  };

  const findSettingsMenu = () => {
    const linked=getLinkedSettingsMenu(lastSettingsTrigger);
    if(linked && looksLikeMainSettingsMenu(linked)) return linked;

    return [...document.querySelectorAll('[role="menu"][data-state="open"]')]
      .filter(visible)
      .find(looksLikeMainSettingsMenu) || null;
  };

  const scoreSettingsTrigger = button => {
    if(!button || !visible(button)) return -999;
    if(button.getAttribute('aria-haspopup')!=='menu') return -999;

    const text=norm(button.textContent);
    const icons=norm([...button.querySelectorAll('i')].map(i=>i.textContent).join(' '));
    let score=0;

    if(/x[1-4]/.test(text)) score+=8;
    if(icons.includes('crop_')) score+=8;
    if(text.includes('video')) score+=3;
    if(text.includes('nano banana')) score+=3;
    if(button.querySelector('[data-type="button-overlay"]')) score+=2;
    if(button.hasAttribute('aria-expanded')) score+=1;

    // Nested model menus normally have model text but no xN/crop info.
    if(text.includes('omni flash') || text.includes('veo 3.1')) score-=2;

    return score;
  };

  const findSettingsTrigger = () => {
    const menu=findSettingsMenu();
    if(menu){
      const id=menu.getAttribute('aria-labelledby');
      const linked=id&&document.getElementById(id);
      if(linked){
        lastSettingsTrigger=linked;
        return linked;
      }
    }

    const candidates=[...document.querySelectorAll('button[aria-haspopup="menu"]')]
      .filter(visible)
      .map(button=>({button,score:scoreSettingsTrigger(button)}))
      .filter(x=>x.score>=8)
      .sort((a,b)=>b.score-a.score);

    const trigger=candidates[0]?.button || null;
    if(trigger) lastSettingsTrigger=trigger;
    return trigger;
  };

  const rectPoint = el => {
    if(!el || !visible(el)) return null;
    const r=el.getBoundingClientRect();
    return {x:r.left+r.width/2,y:r.top+r.height/2,width:r.width,height:r.height};
  };

  const elementLabel = el => {
    if(!el) return '';
    const bits=[
      el.getAttribute?.('aria-label')||'',
      el.getAttribute?.('title')||'',
      el.innerText||'',
      el.textContent||''
    ];
    return bits.join(' ').replace(/\s+/g,' ').trim();
  };

  const iconText = el => [...(el?.querySelectorAll?.('i')||[])].map(i=>i.textContent||'').join(' ').replace(/\s+/g,' ').trim();

  const getProjectInfo = () => {
    const match=String(location.href).match(/\/tools\/flow\/project\/([^/?#]+)/i);
    return {url:location.href,projectId:match?.[1]||null,isProject:Boolean(match?.[1])};
  };

  const findCreateProjectButton = () => {
    if(getProjectInfo().isProject) return null;
    const candidates=[...document.querySelectorAll('button,[role="button"],a')]
      .filter(visible)
      .map(el=>{
        const label=norm(elementLabel(el));
        const icons=norm(iconText(el));
        let score=-999;
        if(/\bnew project\b/.test(label)) score=120;
        else if(/\bcreate (a )?(new )?project\b/.test(label)) score=115;
        else if(/\bstart (a )?(new )?project\b/.test(label)) score=110;
        else if(label==='new' && /add|add_2|create/.test(icons)) score=65;
        else if((label==='create'||label==='new project') && /add|add_2/.test(icons)) score=55;
        // Never confuse the generation Create button with project creation.
        if(/arrow_forward/.test(icons)) score=-999;
        if(/add media|upload media|view images|view videos|all media/.test(label)) score=-999;
        return {el,label,icons,score};
      })
      .filter(x=>x.score>0)
      .sort((a,b)=>b.score-a.score);
    return candidates[0]||null;
  };

  const getCreateProjectPoint = () => {
    const hit=findCreateProjectButton();
    if(!hit){
      const visibleButtons=[...document.querySelectorAll('button,[role="button"],a')]
        .filter(visible).slice(0,30).map(el=>elementLabel(el)).filter(Boolean);
      throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt tÃ¡ÂºÂ¡o Project. URL=${location.href} | buttons=${JSON.stringify(visibleButtons.slice(0,12))}`);
    }
    const point=rectPoint(hit.el);
    if(!point) throw new Error('NÃƒÂºt tÃ¡ÂºÂ¡o Project khÃƒÂ´ng visible.');
    return {...point,label:hit.label,icons:hit.icons};
  };

  const findAllMediaButton = () => [...document.querySelectorAll('button,[role="button"]')]
    .filter(visible)
    .find(el=>{
      const label=norm(elementLabel(el));
      const icons=norm(iconText(el));
      return label==='all media' || label.includes('all media') || icons.split(/\s+/).includes('dashboard');
    }) || null;

  const getAllMediaPoint = () => {
    const button=findAllMediaButton();
    if(!button) throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt All Media trong Project. URL=${location.href}`);
    const point=rectPoint(button);
    if(!point) throw new Error('NÃƒÂºt All Media khÃƒÂ´ng visible.');
    return {...point,label:elementLabel(button),icons:iconText(button)};
  };

  const isAllMediaAvailable = () => !!findAllMediaButton();

  const isSettingsOpen = () => !!findSettingsMenu();

  const getSettingsTriggerPoint = () => {
    const trigger=findSettingsTrigger();
    if(!trigger) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Settings.');
    const point=rectPoint(trigger);
    if(!point) throw new Error('NÃƒÂºt Settings khÃƒÂ´ng visible.');
    return {
      ...point,
      text:trigger.textContent?.replace(/\s+/g,' ').trim()||'',
      ariaExpanded:trigger.getAttribute('aria-expanded'),
      ariaControls:trigger.getAttribute('aria-controls'),
      dataState:trigger.getAttribute('data-state')
    };
  };

  const settingsOpenNow = trigger => {
    const linked=getLinkedSettingsMenu(trigger);
    if(linked && looksLikeMainSettingsMenu(linked)) return linked;
    return findSettingsMenu();
  };

  const dispatchPointerSequence = target => {
    if(!target) return;
    const r=target.getBoundingClientRect();
    const base={
      bubbles:true,
      composed:true,
      cancelable:true,
      clientX:r.left+r.width/2,
      clientY:r.top+r.height/2,
      button:0
    };

    try{
      target.dispatchEvent(new PointerEvent('pointerdown',{
        ...base,pointerId:1,pointerType:'mouse',isPrimary:true,buttons:1
      }));
    }catch{}

    try{
      target.dispatchEvent(new MouseEvent('mousedown',{...base,buttons:1}));
    }catch{}
  };

  const tryOpenSettingsOnce = async (trigger, mode) => {
    const overlay=trigger.querySelector('[data-type="button-overlay"]') || trigger;

    if(mode==='overlay-react-pointer'){
      const x=invoke(overlay,['onPointerDown'],10);
      if(!x) dispatchPointerSequence(overlay);
    }else if(mode==='overlay-dispatch-pointer'){
      dispatchPointerSequence(overlay);
    }else if(mode==='trigger-react-pointer'){
      const x=invoke(trigger,['onPointerDown','onMouseDown','onClick'],8);
      if(!x) trigger.click();
    }else if(mode==='native-click'){
      trigger.click();
    }

    return await waitFor(()=>settingsOpenNow(trigger),800,80);
  };

  const openSettings = async () => {
    let menu=findSettingsMenu();
    if(menu) return menu;

    const trigger=findSettingsTrigger();
    if(!trigger) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Settings.');
    lastSettingsTrigger=trigger;

    const modes=[
      'overlay-react-pointer',
      'overlay-dispatch-pointer',
      'trigger-react-pointer',
      'native-click'
    ];

    for(const mode of modes){
      menu=await tryOpenSettingsOnce(trigger,mode);
      if(menu) return menu;
      await sleep(100);
    }

    const diag={
      text:trigger.textContent?.replace(/\s+/g,' ').trim()||'',
      ariaExpanded:trigger.getAttribute('aria-expanded'),
      ariaControls:trigger.getAttribute('aria-controls'),
      dataState:trigger.getAttribute('data-state'),
      hasOverlay:!!trigger.querySelector('[data-type="button-overlay"]')
    };

    throw new Error(`KhÃƒÂ´ng mÃ¡Â»Å¸ Ã„â€˜Ã†Â°Ã¡Â»Â£c Settings. trigger=${JSON.stringify(diag)}`);
  };

  const findTab = (menu,spec) => [...menu.querySelectorAll('button[role="tab"]')].find(b=>{
    if(spec.suffix) return (b.getAttribute('aria-controls')||'').endsWith(`-content-${spec.suffix}`);
    if(spec.text) return norm(b.textContent)===norm(spec.text);
    return false;
  });

  const selectOption = async (spec,label) => {
    const menu=await openSettings(), tab=findTab(menu,spec);
    if(!tab) throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y tÃƒÂ¹y chÃ¡Â»Ân ${label}.`);
    if(tab.getAttribute('aria-selected')==='true'||tab.getAttribute('data-state')==='active') return false;
    const x=invoke(tab,['onMouseDown','onPointerDown','onClick']); if(!x) tab.click();
    const ok=await waitFor(()=>{
      const m=findSettingsMenu(), t=m&&findTab(m,spec);
      return t&&(t.getAttribute('aria-selected')==='true'||t.getAttribute('data-state')==='active')?t:null;
    },4000);
    if(!ok) throw new Error(`Flow chÃ†Â°a chÃ¡Â»Ân Ã„â€˜Ã†Â°Ã¡Â»Â£c ${label}.`);
    return true;
  };

  const applySettings = async o => {
    const type=o?.type?.toUpperCase(), mode=o?.videoMode?.toUpperCase(), ar=o?.aspectRatio, dur=o?.duration?.toLowerCase(), out=o?.outputs?.toLowerCase();
    if(type==='IMAGE') await selectOption({suffix:'IMAGE'},'Image');
    if(type==='VIDEO') await selectOption({suffix:'VIDEO'},'Video');
    if(mode==='FRAMES') await selectOption({suffix:'VIDEO_FRAMES'},'Frames');
    if(mode==='INGREDIENTS'||mode==='REFERENCES') await selectOption({suffix:'VIDEO_REFERENCES'},'Ingredients');
    if(ar==='9:16') await selectOption({suffix:'PORTRAIT'},'9:16');
    if(ar==='16:9') await selectOption({suffix:'LANDSCAPE'},'16:9');
    // Flow/Veo 3.1 currently exposes some models with a fixed duration (for example
    // Fast reference-to-video is 8s) and therefore renders no duration tab at all.
    // Only try to change duration when the control is actually present.
    if(type==='VIDEO' && ['4s','6s','8s','10s'].includes(dur)){
      const durationMenu=await openSettings();
      if(findTab(durationMenu,{text:dur})) await selectOption({text:dur},dur);
    }
    if(['x1','x2','x3','x4'].includes(out)) await selectOption({text:out},out);
    const tr=findSettingsTrigger();
    return {ok:true,currentSettings:tr?.textContent?.replace(/\s+/g,' ').trim()||null};
  };

  const closeSettings = async () => {
    let menu=findSettingsMenu(); if(!menu) return true;
    const id=menu.getAttribute('aria-labelledby'), tr=(id&&document.getElementById(id))||lastSettingsTrigger||findSettingsTrigger();
    if(tr){
      const overlay=tr.querySelector('[data-type="button-overlay"]')||tr;
      const x=invoke(overlay,['onPointerDown'],10);
      if(!x) dispatchPointerSequence(overlay);
    }
    let closed=await waitFor(()=>!findSettingsMenu(),900); if(closed) return true;
    const active=document.activeElement||document.body;
    const o={key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true,composed:true,cancelable:true};
    active.dispatchEvent(new KeyboardEvent('keydown',o));
    document.dispatchEvent(new KeyboardEvent('keydown',o));
    document.dispatchEvent(new KeyboardEvent('keyup',o));
    closed=await waitFor(()=>!findSettingsMenu(),3000);
    if(!closed) throw new Error('KhÃƒÂ´ng Ã„â€˜ÃƒÂ³ng Ã„â€˜Ã†Â°Ã¡Â»Â£c Settings.');
    return true;
  };

  // SLATE
  const findSlateEl = () => {
    const ph=[...document.querySelectorAll('[data-slate-placeholder="true"]')].find(e=>norm(e.textContent).includes('what do you want to create'));
    const from=ph?.closest('[data-slate-editor="true"][contenteditable="true"]'); if(from) return from;
    return [...document.querySelectorAll('[data-slate-editor="true"][contenteditable="true"]')].filter(visible).sort((a,b)=>{
      const A=a.getBoundingClientRect(),B=b.getBoundingClientRect(); return B.width*B.height-A.width*A.height;
    })[0];
  };
  const isSlate = v => v&&typeof v==='object'&&Array.isArray(v.children)&&typeof v.apply==='function'&&typeof v.insertText==='function';
  const getSlate = el => {
    const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber$')); if(!fk) throw new Error('KhÃƒÂ´ng thÃ¡ÂºÂ¥y React Fiber Slate.');
    const seen=new WeakSet(); let inspected=0;
    const search=(v,d=0)=>{
      if(isSlate(v)) return v;
      if(!v||typeof v!=='object'||d>5||inspected>7000||seen.has(v)||v instanceof Node||v===window||v===document) return null;
      seen.add(v); inspected++;
      let keys; try{keys=Object.keys(v).slice(0,140)}catch{return null}
      keys.sort((a,b)=>(/editor|current|value|state|memoized/i.test(a)?0:1)-(/editor|current|value|state|memoized/i.test(b)?0:1));
      for(const k of keys){ let c; try{c=v[k]}catch{continue}; const f=search(c,d+1); if(f) return f; }
      return null;
    };
    let fiber=el[fk];
    for(let level=0;fiber&&level<90;level++,fiber=fiber.return){
      for(const src of [fiber.memoizedProps,fiber.pendingProps,fiber.memoizedState,fiber.dependencies]){ const e=search(src); if(e) return e; }
    }
    throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y Slate editor object.');
  };
  const leaves=(nodes,path=[],out=[])=>{ nodes.forEach((n,i)=>{const p=[...path,i]; if(typeof n?.text==='string') out.push({path:p,text:n.text}); else if(Array.isArray(n?.children)) leaves(n.children,p,out);}); return out; };
  const slateText=e=>leaves(e.children).map(x=>x.text).join('');
  const replacePrompt = async prompt0 => {
    const prompt=String(prompt0??'').trim(); if(!prompt) throw new Error('Prompt Ã„â€˜ang rÃ¡Â»â€”ng.');
    const el=findSlateEl(); if(!el) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y ÃƒÂ´ prompt Slate.');
    const e=getSlate(el); el.focus(); const ls=leaves(e.children); if(!ls.length) throw new Error('Slate khÃƒÂ´ng cÃƒÂ³ text leaf.');
    const first=ls[0], last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e)&&typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(120); if(slateText(e)) throw new Error(`Clear prompt thÃ¡ÂºÂ¥t bÃ¡ÂºÂ¡i: ${slateText(e)}`);
    e.insertText(prompt); await sleep(450); const final=slateText(e); if(final!==prompt) throw new Error(`Prompt thÃ¡Â»Â±c tÃ¡ÂºÂ¿ khÃƒÂ´ng Ã„â€˜ÃƒÂºng: ${final}`);
    return {ok:true,prompt:final};
  };


  // v14.5.6: composer hygiene. Flow keeps reference chips and Slate text between
  // creates, so every IMAGE/VIDEO submit must start from a verified empty composer.
  const clearPrompt = async () => {
    const el=findSlateEl();
    if(!el) return {ok:true,skipped:true,text:''};
    const e=getSlate(el); el.focus();
    const ls=leaves(e.children); if(!ls.length) return {ok:true,text:''};
    const first=ls[0], last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e) && typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(140);
    const remain=slateText(e);
    if(remain) throw new Error(`Clear prompt thÃ¡ÂºÂ¥t bÃ¡ÂºÂ¡i: ${remain}`);
    return {ok:true,text:''};
  };

  const composerMediaRemoveButtons = () => [...document.querySelectorAll('button[data-card-open]')]
    .filter(visible)
    .filter(b=>b.querySelector('img') && [...b.querySelectorAll('i')].some(i=>norm(i.textContent)==='cancel'));

  // v14.6.1: composer chips are the source of truth for references that are
  // already attached. Flow exposes the real mediaId directly in:
  // /fx/api/trpc/media.getMediaUrlRedirect?name=<uuid>
  // Read it before opening Asset Picker or uploading the same reference again.
  const getComposerMediaState = () => {
    const buttons=composerMediaRemoveButtons();
    const items=buttons.map((b,index)=>{
      const img=b.querySelector('img');
      const src=img?.getAttribute('src')||'';
      return {index,...rectPoint(b),src,mediaId:mediaId(src),title:img?.getAttribute('alt')||''};
    });
    return {count:buttons.length,mediaIds:[...new Set(items.map(x=>x.mediaId).filter(Boolean))],items};
  };

  const getComposerMediaRemovePoint = (wantedMediaId='') => {
    const wanted=String(wantedMediaId||'').trim();
    const buttons=composerMediaRemoveButtons();
    const b=wanted
      ? buttons.find(btn=>mediaId(btn.querySelector('img')?.getAttribute('src'))===wanted)
      : buttons[0];
    if(!b) return null;
    const icon=[...b.querySelectorAll('i')].find(i=>norm(i.textContent)==='cancel');
    const target=icon?.parentElement || icon || b;
    const src=b.querySelector('img')?.getAttribute('src')||null;
    return {...rectPoint(target),src,mediaId:mediaId(src)};
  };

  // CREATE
  const findCreate = () => [...document.querySelectorAll('button')].filter(visible).find(b=>{
    const icons=[...b.querySelectorAll('i')].map(i=>norm(i.textContent));
    const labels=[...b.querySelectorAll('span')].map(s=>norm(s.textContent));
    return icons.includes('arrow_forward')&&labels.includes('create');
  });
  const waitCreateReady = async (timeout=15000) => {
    const b=await waitFor(()=>{ const c=findCreate(); return c&&!c.disabled&&c.getAttribute('aria-disabled')!=='true'?c:null; },timeout,200);
    if(!b){const c=findCreate(); throw new Error(`Create vÃ¡ÂºÂ«n khÃƒÂ³a. aria-disabled=${c?.getAttribute('aria-disabled')}`)}
    return true;
  };
  const getCreatePoint = () => {
    const b=findCreate(); if(!b) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Create.');
    if(b.disabled||b.getAttribute('aria-disabled')==='true') throw new Error('NÃƒÂºt Create Ã„â€˜ang bÃ¡Â»â€¹ khÃƒÂ³a.');
    const r=b.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2,text:b.textContent};
  };

  // ASSETS
  const findSearch = () => { const i=document.querySelector('input#add-menu-input[placeholder="Search assets"]'); return visible(i)?i:null; };
  const findDialog = () => findSearch()?.closest('[role="dialog"]')||null;
  const findAddMedia = () => [...document.querySelectorAll('button[aria-haspopup="dialog"]')]
    .filter(visible)
    .filter(b=>[...b.querySelectorAll('i')].some(i=>norm(i.textContent)==='add_2'))
    .map(b=>({b,r:b.getBoundingClientRect(),text:(b.textContent||'').replace(/\s+/g,' ').trim()}))
    .filter(x=>x.r.left>=0 && x.r.top>=0 && x.r.right<=window.innerWidth && x.r.bottom<=window.innerHeight)
    .filter(x=>{
      const cx=x.r.left+x.r.width/2, cy=x.r.top+x.r.height/2;
      const hit=document.elementFromPoint(cx,cy);
      return hit && (hit===x.b || x.b.contains(hit) || hit.closest?.('button')===x.b);
    })
    .sort((a,b)=>{
      const footerA=/t?o|create/i.test(a.text)?1:0;
      const footerB=/t?o|create/i.test(b.text)?1:0;
      return footerB-footerA || b.r.top-a.r.top || b.r.left-a.r.left;
    })[0]?.b||null;


  // v10.2: expose coordinates/state only. Background performs all critical
  // Asset Picker clicks through chrome.debugger trusted input per tab.
  const isAssetPickerOpen = () => !!findSearch();

  const getAddMediaPoint = () => {
    const b=findAddMedia();
    if(!b) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Add media.');
    return {
      ...rectPoint(b),
      text:(b.textContent||'').replace(/\s+/g,' ').trim(),
      expanded:b.getAttribute('aria-expanded'),
      state:b.getAttribute('data-state')
    };
  };


  const openAssetPicker = async () => {
    if(isAssetPickerOpen()) return true;
    const b=findAddMedia();
    if(!b) throw new Error('Kh????ng t????m th??????y n????t Add media.');
    const overlay=b.querySelector('[data-type="button-overlay"]') || b;
    dispatchPointerSequence(overlay);
    try{ overlay.click(); }catch{}
    try{ b.click(); }catch{}
    return !!(await waitFor(()=>isAssetPickerOpen(),1200,80));
  };

  // v14: find the visible action that opens Flow's native image file chooser.
  // The actual file is injected by background.js through CDP DOM.setFileInputFiles,
  // so page.js only needs to expose a trustworthy click coordinate when Flow lazily
  // creates the <input type=file> after choosing an Upload/From device action.
  const getUploadImagePoint = () => {
    const d=findDialog();
    if(!d) throw new Error('Asset Picker chÃ†Â°a mÃ¡Â»Å¸ khi tÃƒÂ¬m Upload Image.');
    const nodes=[...d.querySelectorAll('button,[role="button"],[role="menuitem"],label')].filter(visible);
    const score = el => {
      const text=norm(elementLabel(el));
      const icons=norm(iconText(el));
      let n=0;
      if(text==='upload image' || text==='upload images') n+=100;
      if(text.includes('upload image')) n+=80;
      if(text.includes('upload media')) n+=70;
      if(text.includes('from device') || text.includes('from computer')) n+=65;
      if(text==='upload' || text.startsWith('upload ')) n+=55;
      if(icons.includes('upload_file') || icons.includes('file_upload') || icons==='upload') n+=45;
      if(icons.includes('add_photo_alternate') && text.includes('upload')) n+=35;
      if(text.includes('video') && !text.includes('image')) n-=80;
      return n;
    };
    const ranked=nodes.map(el=>({el,score:score(el)})).filter(x=>x.score>0).sort((a,b)=>b.score-a.score);
    const target=ranked[0]?.el||null;
    if(!target) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Upload Image / From device trong Asset Picker.');
    return {...rectPoint(target),label:elementLabel(target),icons:iconText(target),score:ranked[0].score};
  };

  const getImagesTab = () => {
    const d=findDialog();
    if(!d) return null;
    return [...d.querySelectorAll('nav[role="tablist"] button[role="tab"]')]
      .find(b=>[...b.querySelectorAll('i')].some(i=>norm(i.textContent)==='image')) || null;
  };

  const isImagesTabSelected = () => {
    const tab=getImagesTab();
    return !!tab && tab.getAttribute('aria-selected')==='true';
  };

  const getImagesTabPoint = () => {
    const tab=getImagesTab();
    if(!tab) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y tab Images trong Asset Picker.');
    return {
      ...rectPoint(tab),
      selected:tab.getAttribute('aria-selected')==='true',
      text:(tab.textContent||'').replace(/\s+/g,' ').trim()
    };
  };

  const setAssetSearch = async prompt => {
    if(!findSearch()) throw new Error('Asset Picker chÃ†Â°a mÃ¡Â»Å¸ khi nhÃ¡ÂºÂ­p Search assets.');
    await setSearch(prompt);
    return {ok:true,prompt:String(prompt??'').trim()};
  };

  const getAssetOptionPoint = id => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId Ã¡ÂºÂ£nh Ã„â€˜ang rÃ¡Â»â€”ng.');
    const d=findDialog();
    if(!d) throw new Error('Asset Picker chÃ†Â°a mÃ¡Â»Å¸.');
    const option=[...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')]
      .filter(visible)
      .find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)) || null;
    if(!option) return null;
    const target=option.querySelector('img')||option;
    return {
      ...rectPoint(target),
      mediaId:wanted,
      title:option.querySelector('img')?.getAttribute('alt')||'',
      selected:option.getAttribute('aria-selected')==='true',
      dataIndex:option.closest('[data-index]')?.getAttribute('data-index')||null
    };
  };

  const openPicker = async () => {
    let input=findSearch(); if(input) return input;
    await closeSettings().catch(()=>{});
    const b=findAddMedia(); if(!b) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Add media.');
    const x=invoke(b,['onClick','onPointerDown','onMouseDown']); if(!x) b.click();
    input=await waitFor(findSearch,6000); if(!input) throw new Error('Asset Picker khÃƒÂ´ng mÃ¡Â»Å¸.');
    return input;
  };

  const closePicker = async () => {
    if(!findSearch()) return true;
    const b=findAddMedia();
    if(b&&b.getAttribute('aria-expanded')==='true'){ const x=invoke(b,['onClick','onPointerDown','onMouseDown']); if(!x) b.click(); }
    let closed=await waitFor(()=>!findSearch(),1000); if(closed) return true;
    const active=document.activeElement||document.body, o={key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true,composed:true,cancelable:true};
    active.dispatchEvent(new KeyboardEvent('keydown',o)); document.dispatchEvent(new KeyboardEvent('keydown',o)); document.dispatchEvent(new KeyboardEvent('keyup',o));
    closed=await waitFor(()=>!findSearch(),3000); if(!closed) throw new Error('KhÃƒÂ´ng Ã„â€˜ÃƒÂ³ng Ã„â€˜Ã†Â°Ã¡Â»Â£c Asset Picker.');
    return true;
  };

  const selectImagesTab = async () => {
    const d=findDialog(); if(!d) throw new Error('Asset Picker chÃ†Â°a mÃ¡Â»Å¸.');
    const tab=[...d.querySelectorAll('nav[role="tablist"] button[role="tab"]')].find(b=>[...b.querySelectorAll('i')].some(i=>norm(i.textContent)==='image'));
    if(!tab) return false; if(tab.getAttribute('aria-selected')==='true') return true;
    const x=invoke(tab,['onMouseDown','onClick','onPointerDown']); if(!x) tab.click();
    const selected=await waitFor(()=>tab.getAttribute('aria-selected')==='true'?tab:null,2500);
    if(!selected) throw new Error('Ã„ÂÃƒÂ£ click tab Images nhÃ†Â°ng Flow chÃ†Â°a chÃ¡Â»Ân Images.');
    return true;
  };

  const setSearch = async q => {
    let i=findSearch(); if(!i){await openPicker(); i=findSearch();}
    if(!i) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y Search assets.');
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set; if(!setter) throw new Error('KhÃƒÂ´ng lÃ¡ÂºÂ¥y Ã„â€˜Ã†Â°Ã¡Â»Â£c input setter.');
    i.focus(); setter.call(i,''); i.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'deleteContentBackward'})); await sleep(80);
    const text=String(q??'').trim(); setter.call(i,text); i.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'insertText',data:text})); i.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
    await sleep(500); return true;
  };

  // v14.6.1: Flow asset thumbnails do not expose generated media IDs in one stable URL shape.
  // Older/current assets may use ?name=<uuid>, ?mediaId=<uuid>, or a CDN path such as
  // /image/<uuid>?Expires=... . The old parser only handled ?name=, so the right image
  // could be visible while the bridge still returned mediaId=null.
  const mediaId = src => {
    const raw=String(src||'').trim();
    if(!raw) return null;
    const uuidRe=/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;
    try{
      const u=new URL(raw,location.origin);
      for(const key of ['name','mediaId','media_id','id']){
        const v=u.searchParams.get(key);
        const m=String(v||'').match(uuidRe);
        if(m) return m[0];
      }
      const decoded=decodeURIComponent(u.pathname||'');
      const pathMatch=decoded.match(uuidRe);
      if(pathMatch) return pathMatch[0];
    }catch{}
    try{
      const decoded=decodeURIComponent(raw);
      const m=decoded.match(uuidRe);
      if(m) return m[0];
    }catch{}
    const m=raw.match(uuidRe);
    return m?m[0]:null;
  };

  const optionHasMediaId = (option,wanted) => {
    if(!option||!wanted) return false;
    const nodes=[option,...option.querySelectorAll('*')];
    for(const el of nodes){
      for(const a of [...(el.attributes||[])]){
        const v=String(a.value||'');
        if(v===wanted || v.includes(wanted) || mediaId(v)===wanted) return true;
      }
      // Flow often keeps media metadata only in React props instead of DOM attributes.
      // Scan a shallow bounded object graph for the exact UUID; never stringify the whole tree.
      const root=propsOf(el);
      if(root){
        const seen=new Set(), stack=[{v:root,d:0}];
        let budget=0;
        while(stack.length && budget++<240){
          const {v,d}=stack.pop();
          if(typeof v==='string'){ if(v===wanted||v.includes(wanted)||mediaId(v)===wanted) return true; continue; }
          if(!v||typeof v!=='object'||d>=4||seen.has(v)) continue;
          seen.add(v);
          for(const key of Object.keys(v).slice(0,50)){
            let child; try{child=v[key]}catch{continue}
            if(typeof child==='string'){ if(child===wanted||child.includes(wanted)||mediaId(child)===wanted) return true; }
            else if(child&&typeof child==='object') stack.push({v:child,d:d+1});
          }
        }
      }
    }
    return false;
  };

  const collectImages = () => {
    const d=findDialog(); if(!d) return [];
    return [...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')]
      .filter(visible).filter(o=>!!o.querySelector('img'))
      .sort((a,b)=>Number(a.closest('[data-index]')?.getAttribute('data-index')??999999)-Number(b.closest('[data-index]')?.getAttribute('data-index')??999999))
      .map(o=>{const img=o.querySelector('img'),src=img?.getAttribute('src')||'';return {mediaId:mediaId(src),title:img?.getAttribute('alt')||'',src,dataIndex:o.closest('[data-index]')?.getAttribute('data-index')||null,selected:o.getAttribute('aria-selected')==='true'}});
  };

  const snapshotImagesByPrompt = async prompt => {
    await closeSettings().catch(()=>{}); await openPicker(); await selectImagesTab(); await setSearch(prompt); await sleep(800);
    const items=collectImages(), ids=items.map(x=>x.mediaId).filter(Boolean); await closePicker(); return {ids,items};
  };

  const prepareNewImageSearch = async prompt => { await closeSettings().catch(()=>{}); await openPicker(); await selectImagesTab(); await setSearch(prompt); return true; };
  const listSearchedImages = () => collectImages();

  const selectImageByMediaId = async id => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId Ã¡ÂºÂ£nh Ã„â€˜ang rÃ¡Â»â€”ng.');

    const findOption = () => {
      const d=findDialog(); if(!d) return null;
      return [...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')]
        .filter(visible)
        .find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)) || null;
    };

    let option=findOption();
    if(!option) throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y Ã¡ÂºÂ£nh mediaId=${wanted}`);

    const clickCurrent = () => {
      option=findOption();
      if(!option) return false;
      const img=option.querySelector('img'), target=img||option;
      const x=invoke(target,['onClick','onMouseDown','onPointerDown']);
      if(!x) target.click();
      return true;
    };

    clickCurrent();
    let closed=await waitFor(()=>!findSearch(),2500);
    if(!closed){
      if(!clickCurrent()) throw new Error('Ã¡ÂºÂ¢nh biÃ¡ÂºÂ¿n mÃ¡ÂºÂ¥t khÃ¡Â»Âi danh sÃƒÂ¡ch trÃ†Â°Ã¡Â»â€ºc click lÃ¡ÂºÂ§n 2.');
      closed=await waitFor(()=>!findSearch(),4000);
    }
    if(!closed) throw new Error('Ã„ÂÃƒÂ£ chÃ¡Â»Ân Ã¡ÂºÂ£nh nhÃ†Â°ng Asset Picker chÃ†Â°a Ã„â€˜ÃƒÂ³ng.');
    return {ok:true,mediaId:wanted};
  };



  const MODEL_ALIASES = {
    IMAGE: {
      "Nano Banana Pro": [
        "Nano Banana Pro",
        "Banana Pro"
      ],
      "Nano Banana 2 Lite": [
        "Nano Banana 2 Lite",
        "Banana 2 Lite"
      ],
      "Nano Banana 2": [
        "Nano Banana 2",
        "Banana 2"
      ]
    },

    VIDEO: {
      "Veo 3.1 - Lite": [
        "Veo 3.1 - Lite",
        "Veo 3.1 Lite",
        "Veo Lite"
      ],
      "Veo 3.1 - Lite [Lower Priority]": [
        "Veo 3.1 - Lite [Lower Priority]",
        "Veo 3.1 Lite [Lower Priority]",
        "Veo Lite [Lower Priority]"
      ],
      "Veo 3.1 - Fast": [
        "Veo 3.1 - Fast",
        "Veo 3.1 Fast",
        "Veo Fast"
      ],
      "Veo 3.1 - Quality": [
        "Veo 3.1 - Quality",
        "Veo 3.1 Quality",
        "Veo Quality"
      ],
      "Gemini Omni Flash": [
        "Gemini Omni Flash",
        "Omni Flash"
      ]
    }
  };

  const modelAliasesFor = (kind, requested) => {
    const group = MODEL_ALIASES[kind] || {};
    return group[requested] || [requested];
  };

  const canonicalModelText = value => String(value??'')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g,' ')
    .replace(/\s+/g,' ')
    .trim();

  const semanticText = el => {
    if(!el) return '';
    const clone=el.cloneNode(true);
    clone.querySelectorAll?.('i,.google-symbols,.material-icons,[aria-hidden="true"]').forEach(n=>n.remove());
    return clone.textContent?.replace(/\s+/g,' ').trim()||'';
  };

  const textMatchesAnyAlias = (text, aliases) => {
    const t=canonicalModelText(text);
    return aliases.some(alias=>t===canonicalModelText(alias));
  };

  const findNestedModelTrigger = (kind) => {
    const menu = findSettingsMenu();
    if (!menu) return null;

    const buttons = [...menu.querySelectorAll("button")].filter(visible);

    // Prefer a nested menu trigger that currently shows a known model name.
    const knownAliases = Object.values(MODEL_ALIASES[kind] || {}).flat();

    const byKnownModel = buttons.find(button =>
      textMatchesAnyAlias(semanticText(button), knownAliases)
    );

    if (byKnownModel) return byKnownModel;

    // Fallback to nested aria-haspopup=menu button, excluding outer settings trigger.
    return buttons.find(button =>
      button.getAttribute("aria-haspopup") === "menu" &&
      button !== findSettingsTrigger()
    ) || null;
  };

  const selectModel = async (kind, requestedModel) => {
    const requested = String(requestedModel ?? "CURRENT").trim();
    const upper = requested.toUpperCase();

    if (!requested) {
      throw new Error("ChÃ†Â°a chÃ¡Â»Ân model.");
    }

    if (upper === "NONE") {
      return { ok: true, changed: false, model: "NONE" };
    }

    if (kind !== "IMAGE" && kind !== "VIDEO") {
      throw new Error(`kind model khÃƒÂ´ng hÃ¡Â»Â£p lÃ¡Â»â€¡: ${kind}`);
    }

    if (kind === "IMAGE") {
      await selectOption({ suffix: "IMAGE" }, "Image");
    } else {
      await selectOption({ suffix: "VIDEO" }, "Video");
    }

    await openSettings();

    const aliases = modelAliasesFor(kind, requested);
    const trigger = findNestedModelTrigger(kind);

    if (!trigger) {
      throw new Error(
        `KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt chÃ¡Â»Ân model ${kind === "IMAGE" ? "Ã¡ÂºÂ£nh" : "video"}.`
      );
    }

    const current = semanticText(trigger);

    if (textMatchesAnyAlias(current, aliases)) {
      return {
        ok: true,
        changed: false,
        model: requested,
        current
      };
    }

    let invoked = invoke(
      trigger,
      ["onPointerDown", "onMouseDown", "onClick"]
    );

    if (!invoked) trigger.click();

    await sleep(300);

    const layers = [
      ...document.querySelectorAll(
        '[role="menu"][data-state="open"], [role="listbox"], [data-radix-menu-content]'
      )
    ].filter(visible);

    const searchRoot = layers.at(-1) || document;

    const candidates = [
      ...searchRoot.querySelectorAll(
        '[role="menuitem"], [role="option"], button, [role="button"], div'
      )
    ]
      .filter(visible)
      .filter(el => textMatchesAnyAlias(semanticText(el), aliases))
      .sort((a, b) => {
        const exactA = aliases.some(alias => canonicalModelText(semanticText(a)) === canonicalModelText(alias)) ? 0 : 1;
        const exactB = aliases.some(alias => canonicalModelText(semanticText(b)) === canonicalModelText(alias)) ? 0 : 1;

        if (exactA !== exactB) return exactA - exactB;

        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();

        return ra.width * ra.height - rb.width * rb.height;
      });

    if (!candidates.length) {
      throw new Error(
        `KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y model "${requested}" trong menu Flow.`
      );
    }

    const target =
      candidates[0].closest(
        '[role="menuitem"], [role="option"], button, [role="button"]'
      ) || candidates[0];

    invoked = invoke(
      target,
      ["onMouseDown", "onPointerDown", "onClick"]
    );

    if (!invoked) target.click();

    await sleep(400);

    return {
      ok: true,
      changed: true,
      model: requested
    };
  };



  const getModelTriggerPoint = (kind, requestedModel) => {
    const requested=String(requestedModel||'').trim();
    if(!requested) throw new Error('ChÃ†Â°a chÃ¡Â»Ân model.');
    const menu=findSettingsMenu();
    if(!menu) throw new Error('Settings chÃ†Â°a mÃ¡Â»Å¸ khi lÃ¡ÂºÂ¥y model trigger.');
    const trigger=findNestedModelTrigger(String(kind||'').toUpperCase());
    if(!trigger) throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt chÃ¡Â»Ân model ${kind}.`);
    const current=semanticText(trigger);
    const aliases=modelAliasesFor(String(kind||'').toUpperCase(),requested);
    const point=rectPoint(trigger);
    return {alreadySelected:textMatchesAnyAlias(current,aliases),current,...point};
  };

  const getModelOptionPoint = (kind, requestedModel) => {
    const requested=String(requestedModel||'').trim();
    const aliases=modelAliasesFor(String(kind||'').toUpperCase(),requested);
    const layers=[...document.querySelectorAll('[role="menu"][data-state="open"],[role="listbox"],[data-radix-menu-content]')].filter(visible);
    const main=findSettingsMenu();
    const roots=layers.filter(layer=>layer!==main);
    const root=roots.at(-1)||layers.at(-1)||document;
    const candidates=[...root.querySelectorAll('[role="menuitem"],[role="option"],button,[role="button"]')]
      .filter(visible)
      .filter(el=>textMatchesAnyAlias(semanticText(el),aliases))
      .sort((a,b)=>{
        const A=a.getBoundingClientRect(),B=b.getBoundingClientRect();
        return A.width*A.height-B.width*B.height;
      });
    const target=candidates[0]||null;
    if(!target) throw new Error(`KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y option model "${requested}".`);
    return {label:semanticText(target),...rectPoint(target)};
  };

  const specSelected = (menu, spec) => {
    const tab=findTab(menu,spec);
    return !!tab && (
      tab.getAttribute('aria-selected')==='true' ||
      tab.getAttribute('data-state')==='active'
    );
  };

  const verifyStageSettings = async options => {
    const type=String(options?.type||'').toUpperCase();
    const mode=String(options?.videoMode||'').toUpperCase();
    const ar=String(options?.aspectRatio||'');
    const dur=String(options?.duration||'').toLowerCase();
    const outputs=String(options?.outputs||'').toLowerCase();
    const modelKind=String(options?.modelKind||type).toUpperCase();
    const requestedModel=String(options?.model||'').trim();

    const menu=await openSettings();
    const checks={};

    if(type==='IMAGE') checks.type=specSelected(menu,{suffix:'IMAGE'});
    else if(type==='VIDEO') checks.type=specSelected(menu,{suffix:'VIDEO'});
    else checks.type=false;

    if(type==='VIDEO' && mode==='INGREDIENTS') checks.videoMode=specSelected(menu,{suffix:'VIDEO_REFERENCES'});
    else if(type==='VIDEO' && mode==='FRAMES') checks.videoMode=specSelected(menu,{suffix:'VIDEO_FRAMES'});
    else checks.videoMode=true;

    if(ar==='16:9') checks.aspectRatio=specSelected(menu,{suffix:'LANDSCAPE'});
    else if(ar==='9:16') checks.aspectRatio=specSelected(menu,{suffix:'PORTRAIT'});
    else checks.aspectRatio=true;

    // Duration is relevant only for VIDEO. Some Veo/Flow variants expose no
    // duration control because the selected model fixes the length. Presence of the
    // control is diagnostic information, NOT a setting that must itself be true.
    let durationControlPresent=false;
    if(type==='VIDEO' && ['4s','6s','8s','10s'].includes(dur)){
      const durationTab=findTab(menu,{text:dur});
      durationControlPresent=!!durationTab;
      checks.duration=durationTab ? specSelected(menu,{text:dur}) : true;
    }else{
      checks.duration=true;
    }

    if(['x1','x2','x3','x4'].includes(outputs)) checks.outputs=specSelected(menu,{text:outputs});
    else checks.outputs=true;

    let modelText='';
    if(requestedModel && requestedModel.toUpperCase()!=='NONE'){
      const trigger=findNestedModelTrigger(modelKind);
      modelText=semanticText(trigger);
      checks.model=!!trigger && textMatchesAnyAlias(modelText,modelAliasesFor(modelKind,requestedModel));
    }else{
      checks.model=true;
    }

    const settingsTrigger=findSettingsTrigger();
    const currentSettings=settingsTrigger?.textContent?.replace(/\s+/g,' ').trim()||'';
    const failed=Object.entries(checks).filter(([,ok])=>!ok).map(([name])=>name);

    return {
      ok:failed.length===0,
      checks,
      failed,
      observed:{durationControlPresent},
      requested:{type,videoMode:mode,aspectRatio:ar,duration:dur,outputs,modelKind,model:requestedModel},
      current:{settings:currentSettings,model:modelText}
    };
  };

  const ensureStageSettings = async (options, maxAttempts=3) => {
    const attempts=[];

    for(let attempt=1;attempt<=maxAttempts;attempt++){
      // Always write every requested setting again. Do not trust prior page state.
      await applySettings(options);

      const requestedModel=String(options?.model||'').trim();
      const modelKind=String(options?.modelKind||options?.type||'').toUpperCase();
      if(requestedModel && requestedModel.toUpperCase()!=='NONE'){
        await selectModel(modelKind,requestedModel);
      }

      await sleep(350);
      const verification=await verifyStageSettings(options);
      attempts.push({attempt,...verification});

      if(verification.ok){
        await closeSettings();
        return {
          ok: true,
          attempt,
          verification: {
            ok: verification.ok,
            checks: { ...verification.checks },
            failed: [...verification.failed],
            observed: { ...(verification.observed||{}) },
            requested: { ...verification.requested },
            current: { ...verification.current }
          },
          attempts: attempts.map(item => ({
            attempt: item.attempt,
            ok: item.ok,
            failed: [...item.failed],
            observed: { ...(item.observed||{}) },
            current: { ...item.current }
          }))
        };
      }

      // Keep page clean before retrying. If a nested model menu remains, Escape first.
      const active=document.activeElement||document.body;
      const esc={key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true,composed:true,cancelable:true};
      active.dispatchEvent(new KeyboardEvent('keydown',esc));
      document.dispatchEvent(new KeyboardEvent('keyup',esc));
      await sleep(200);
      await closeSettings().catch(()=>{});
      await sleep(250);
    }

    const last=attempts.at(-1);
    throw new Error(
      `SETTINGS VERIFY FAILED: ${last?.failed?.join(', ')||'unknown'} | `+
      `current=${JSON.stringify(last?.current||{})}`
    );
  };

  const waitAndSelectImageByMediaId = async (prompt, id, timeout=30000) => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId Ã¡ÂºÂ£nh Ã„â€˜ang rÃ¡Â»â€”ng.');

    await closeSettings().catch(()=>{});
    await openPicker();
    await selectImagesTab();
    await setSearch(prompt);

    const started=Date.now();
    let refreshAt=0;

    while(Date.now()-started<timeout){
      const items=collectImages();
      const hit=items.find(x=>x.mediaId===wanted);

      if(hit){
        return await selectImageByMediaId(wanted);
      }

      if(Date.now()-refreshAt>2500){
        refreshAt=Date.now();
        await setSearch(prompt);
      }

      await sleep(350);
    }

    throw new Error(`Ã¡ÂºÂ¢nh Ã„â€˜ÃƒÂ£ SUCCESS nhÃ†Â°ng Asset Picker chÃ†Â°a thÃ¡ÂºÂ¥y mediaId=${wanted}`);
  };



  // v14.5: locate the exact generated video card in All Media by Flow mediaId.
  // Flow's visible title (mediaMetadata.mediaTitle) is only used to filter/search;
  // mediaId remains the correlation key so duplicated prompts/titles cannot select the wrong clip.
  const findGlobalAssetSearch = () => {
    const i=document.querySelector('input[data-testid="search-input"][type="text"]');
    return visible(i)?i:null;
  };

  const setNativeInputValue = (input,value) => {
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
    if(setter) setter.call(input,String(value??'')); else input.value=String(value??'');
    input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:String(value??'')}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
  };

  const setGlobalAssetSearch = async value => {
    const input=await waitFor(findGlobalAssetSearch,8000,120);
    if(!input) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y ÃƒÂ´ Search assets cÃ¡Â»Â§a All Media.');
    input.focus();
    setNativeInputValue(input,String(value??''));
    await sleep(650);
    return {ok:true,value:input.value};
  };

  const mediaIdFromVideoSrc = src => {
    try{
      const u=new URL(String(src||''),location.origin);
      return u.searchParams.get('name')||null;
    }catch{return null;}
  };

  const videoTileInfo = tile => {
    if(!tile) return null;
    const video=tile.querySelector('video[src]');
    const rawSrc=video?.getAttribute('src')||video?.src||'';
    const mediaId=mediaIdFromVideoSrc(rawSrc);
    const href=tile.querySelector('a[href*="/edit/"]')?.getAttribute('href')||null;
    const titleNode=[...tile.querySelectorAll('div')].find(el=>{
      const cls=String(el.className||'');
      return cls.includes('sc-899ba078-3') && norm(el.textContent);
    });
    const title=(titleNode?.textContent||'').replace(/\s+/g,' ').trim() || null;
    return {
      mediaId,
      src:rawSrc||null,
      currentSrc:video?.currentSrc||null,
      tileId:tile.getAttribute('data-tile-id')||tile.id||null,
      href,
      title,
      ...rectPoint(tile)
    };
  };

  const listVisibleVideoTiles = () => [...document.querySelectorAll('[data-tile-id]')]
    .filter(visible)
    .filter(tile=>tile.querySelector('video[src]'))
    .map(videoTileInfo)
    .filter(Boolean);

  const getVideoTileInfoByMediaId = mediaId0 => {
    const wanted=String(mediaId0||'').trim();
    if(!wanted) throw new Error('video mediaId Ã„â€˜ang rÃ¡Â»â€”ng.');
    return listVisibleVideoTiles().find(x=>x.mediaId===wanted)||null;
  };

  const getVideoTileInfoByTitle = title0 => {
    const wanted=norm(title0);
    if(!wanted) return {match:null,count:0,matches:[]};
    const matches=listVisibleVideoTiles().filter(x=>norm(x.title)===wanted);
    return {match:matches[0]||null,count:matches.length,matches};
  };

  const findSignedVideoResource = mediaId0 => {
    const wanted=String(mediaId0||'').trim();
    if(!wanted) return null;
    const urls=[];
    try{
      for(const e of performance.getEntriesByType('resource')||[]){
        const u=String(e?.name||'');
        if((u.includes('flow-content.google')||u.includes('googleusercontent.com')) && (u.includes(wanted)||u.includes(encodeURIComponent(wanted)))) urls.push(u);
      }
    }catch{}
    const tile=getVideoTileInfoByMediaId(wanted);
    for(const u of [tile?.currentSrc,tile?.src]){
      if(u && (u.includes('flow-content.google')||u.includes('googleusercontent.com'))) urls.push(u);
    }
    const url=urls.at(-1)||null;
    return url?{url,mediaId:wanted,source:'performance_or_video'}:null;
  };

  const probeVideoRedirect = async mediaId0 => {
    const wanted=String(mediaId0||'').trim();
    if(!wanted) throw new Error('mediaId video rÃ¡Â»â€”ng.');
    const redirectUrl=`https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(wanted)}`;
    const video=document.createElement('video');
    video.muted=true; video.preload='metadata'; video.playsInline=true;
    video.setAttribute('aria-hidden','true');
    video.style.cssText='position:fixed;width:1px;height:1px;left:-9999px;top:-9999px;opacity:0;pointer-events:none';
    (document.body||document.documentElement).appendChild(video);
    let status='timeout'; let detail='';
    try{
      status=await new Promise(resolve=>{
        let done=false;
        const finish=v=>{if(done)return;done=true;resolve(v);};
        const timer=setTimeout(()=>finish('timeout'),7000);
        video.addEventListener('loadedmetadata',()=>{clearTimeout(timer);finish('loadedmetadata');},{once:true});
        video.addEventListener('canplay',()=>{clearTimeout(timer);finish('canplay');},{once:true});
        video.addEventListener('error',()=>{detail=String(video.error?.message||video.error?.code||'video error');clearTimeout(timer);finish('error');},{once:true});
        video.src=redirectUrl;
        try{video.load();}catch{}
      });
      return {ok:status!=='error',status,detail,mediaId:wanted,redirectUrl};
    }finally{
      try{video.pause();}catch{}
      try{video.removeAttribute('src');video.load();}catch{}
      video.remove();
    }
  };

  const getAddClipPoint = () => {
    const b=[...document.querySelectorAll('button[data-add-button="true"]')].filter(visible)
      .find(x=>norm(x.textContent).includes('add clip')) || null;
    if(!b) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y nÃƒÂºt Add Clip.');
    return {...rectPoint(b),text:(b.textContent||'').replace(/\s+/g,' ').trim(),expanded:b.getAttribute('aria-expanded'),state:b.getAttribute('data-state')};
  };

  const getExtendMenuPoint = () => {
    const items=[...document.querySelectorAll('[role="menuitem"]')].filter(visible);
    const b=items.find(el=>{
      const t=norm(el.textContent), icons=norm(iconText(el));
      return t.includes('extend') && (icons.includes('keyboard_double_arrow_right') || t.includes('veo 3.1'));
    }) || items.find(el=>norm(el.textContent).includes('extend')) || null;
    if(!b) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y menu Extend.');
    return {...rectPoint(b),text:(b.textContent||'').replace(/\s+/g,' ').trim()};
  };

  const findExtendSlateEl = () => {
    const ph=[...document.querySelectorAll('[data-slate-placeholder="true"]')]
      .find(e=>norm(e.textContent).includes('what happens next'));
    const from=ph?.closest('[data-slate-editor="true"][contenteditable="true"]');
    return visible(from)?from:null;
  };

  const isExtendComposerOpen = () => !!findExtendSlateEl();

  const replaceExtendPrompt = async prompt0 => {
    const prompt=String(prompt0??'').trim();
    if(!prompt) throw new Error('Prompt kÃƒÂ©o dÃƒÂ i video Ã„â€˜ang rÃ¡Â»â€”ng.');
    const el=await waitFor(findExtendSlateEl,8000,120);
    if(!el) throw new Error('KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y ÃƒÂ´ What happens next?');
    const e=getSlate(el); el.focus(); const ls=leaves(e.children); if(!ls.length) throw new Error('Extend Slate khÃƒÂ´ng cÃƒÂ³ text leaf.');
    const first=ls[0],last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e)&&typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(100);
    if(slateText(e)) throw new Error(`Clear extend prompt thÃ¡ÂºÂ¥t bÃ¡ÂºÂ¡i: ${slateText(e)}`);
    e.insertText(prompt); await sleep(350);
    const final=slateText(e); if(final!==prompt) throw new Error(`Extend prompt thÃ¡Â»Â±c tÃ¡ÂºÂ¿ khÃƒÂ´ng Ã„â€˜ÃƒÂºng: ${final}`);
    return {ok:true,prompt:final};
  };

  window.FlowPairAuto={getVersion:()=>FLOW_PAIR_AUTO_VERSION,getProjectInfo,getCreateProjectPoint,getAllMediaPoint,isAllMediaAvailable,isSettingsOpen,getSettingsTriggerPoint,openSettings:async()=>{await openSettings();return true;},getModelTriggerPoint,getModelOptionPoint,selectModel,applySettings,verifyStageSettings,ensureStageSettings,closeSettings,replacePrompt,clearPrompt,getComposerMediaState,getComposerMediaRemovePoint,waitCreateReady,getCreatePoint,isAssetPickerOpen,getAddMediaPoint,openAssetPicker,getUploadImagePoint,isImagesTabSelected,getImagesTabPoint,setAssetSearch,getAssetOptionPoint,snapshotImagesByPrompt,prepareNewImageSearch,listSearchedImages,selectImageByMediaId,waitAndSelectImageByMediaId,closeAssetPicker:closePicker,findGlobalAssetSearch:()=>!!findGlobalAssetSearch(),setGlobalAssetSearch,listVisibleVideoTiles,getVideoTileInfoByMediaId,getVideoTileInfoByTitle,findSignedVideoResource,probeVideoRedirect,getAddClipPoint,getExtendMenuPoint,isExtendComposerOpen,replaceExtendPrompt};
})();


