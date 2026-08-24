(() => {
  const FLOW_PAIR_AUTO_VERSION = '14.8.0';
  if (window.__FLOW_PAIR_AUTO_VERSION__ === FLOW_PAIR_AUTO_VERSION && window.FlowPairAuto) {
    try { window.FlowPairAuto.resumeAll(); } catch {}
    return;
  }
  window.__FLOW_PAIR_AUTO_VERSION__ = FLOW_PAIR_AUTO_VERSION;
  // Deliberately ignore legacy __FLOW_PAIR_AUTO_V1__. Older extension versions left it behind.

  const PAGE_ABORT = {aborted:false,reason:'',epoch:0,pending:new Set()};
  const abortError = () => new Error(`PAGE AUTOMATION ABORTED Â· ${PAGE_ABORT.reason||'server_offline'}`);
  const assertPageActive = () => { if(PAGE_ABORT.aborted) throw abortError(); return true; };
  const sleep = ms => new Promise((resolve,reject)=>{
    if(PAGE_ABORT.aborted){reject(abortError());return;}
    const epoch=PAGE_ABORT.epoch;
    const item={timer:null,reject};
    item.timer=setTimeout(()=>{
      PAGE_ABORT.pending.delete(item);
      if(PAGE_ABORT.aborted||epoch!==PAGE_ABORT.epoch) reject(abortError());
      else resolve();
    },Math.max(0,Number(ms||0)));
    PAGE_ABORT.pending.add(item);
  });
  const abortAll = reason => {
    PAGE_ABORT.aborted=true;
    PAGE_ABORT.reason=String(reason||'server_offline');
    PAGE_ABORT.epoch++;
    const err=abortError();
    for(const item of [...PAGE_ABORT.pending]){
      clearTimeout(item.timer);PAGE_ABORT.pending.delete(item);
      try{item.reject(err);}catch{}
    }
    try{document.documentElement?.setAttribute('data-flow-pair-aborted','1');}catch{}
    return {ok:true,aborted:true,reason:PAGE_ABORT.reason,epoch:PAGE_ABORT.epoch};
  };
  const resumeAll = () => {
    PAGE_ABORT.aborted=false;PAGE_ABORT.reason='';PAGE_ABORT.epoch++;
    try{document.documentElement?.removeAttribute('data-flow-pair-aborted');}catch{}
    return {ok:true,aborted:false,epoch:PAGE_ABORT.epoch};
  };
  const getAbortState = () => ({aborted:PAGE_ABORT.aborted,reason:PAGE_ABORT.reason,epoch:PAGE_ABORT.epoch,pending:PAGE_ABORT.pending.size});
  const norm = v => String(v ?? '').replace(/\\s+/g,' ').trim().toLowerCase();
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  };
  const waitFor = async (fn, timeout=8000, step=100) => {
    const t=Date.now();
    while(Date.now()-t<timeout){ assertPageActive(); const x=fn(); if(x) return x; await sleep(step); }
    assertPageActive();
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
    assertPageActive();
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

  const isAgentToggle = button => {
    if(!button || !visible(button)) return false;
    const content=norm(button.querySelector?.('.content')?.textContent || button.textContent || '');
    return content==='agent' && button.hasAttribute('aria-pressed');
  };

  const findAgentToggle = () => [...document.querySelectorAll('button[aria-pressed]')]
    .filter(visible)
    .find(isAgentToggle) || null;

  const scoreSettingsTrigger = button => {
    if(!button || !visible(button)) return -999;
    if(isAgentToggle(button) || norm(button.textContent)==='agent') return -999;
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
      if(linked && !isAgentToggle(linked) && norm(linked.textContent)!=='agent'){
        lastSettingsTrigger=linked;
        return linked;
      }
    }

    const candidates=[...document.querySelectorAll('button[aria-haspopup="menu"]')]
      .filter(visible)
      .filter(button=>!isAgentToggle(button) && norm(button.textContent)!=='agent')
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

  const getAgentModeState = () => {
    const button=findAgentToggle();
    if(!button) return {found:false,pressed:false,point:null};
    const pressed=button.getAttribute('aria-pressed')==='true';
    const point=rectPoint(button);
    return {
      found:true,
      pressed,
      ariaPressed:button.getAttribute('aria-pressed'),
      point:point?{...point}:null,
      text:(button.textContent||'').replace(/\s+/g,' ').trim(),
      className:String(button.className||'')
    };
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
    const match=String(location.href).match(/\/flow\/project\/([^/?#]+)/);
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
        else if(!getProjectInfo().isProject && icons.includes('add_2')){
          const r=el.getBoundingClientRect();
          if(r.width>=120 && r.height>=80) score=100;
        }
        // Never confuse the generation Create button with project creation.
        if(/arrow_forward/.test(icons)) score=-999;
        if(/add media|upload media|view images|view videos|all media|th?m n?i dung|h?nh ?nh|video/.test(label)) score=-999;
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
      throw new Error(`KhÃ´ng tÃ¬m tháº¥y nÃºt táº¡o Project. URL=${location.href} | buttons=${JSON.stringify(visibleButtons.slice(0,12))}`);
    }
    const point=rectPoint(hit.el);
    if(!point) throw new Error('NÃºt táº¡o Project khÃ´ng visible.');
    return {...point,label:hit.label,icons:hit.icons};
  };

  const findAllMediaButton = () => [...document.querySelectorAll('button,[role="button"],a')]
    .filter(visible)
    .find(el=>{
      const label=norm(elementLabel(el));
      const icons=norm(iconText(el));
      if (label.includes('trash') || label.includes('thùng rác') || label.includes('thung rac') || icons.includes('delete')) return false;
      return label==='all media' || label.includes('all media') || label.includes('tất cả nội dung') || label.includes('tat ca noi dung') || label.includes('tất cả') || icons.split(/\s+/).includes('dashboard');
    }) || null;

  const getAllMediaPoint = () => {
    const button=findAllMediaButton();
    if(!button) throw new Error(`Không tìm thấy nút All Media trong Project. URL=${location.href}`);
    const point=rectPoint(button);
    if(!point) throw new Error('Nút All Media không visible.');
    return {...point,label:elementLabel(button),icons:iconText(button)};
  };

  const isAllMediaAvailable = () => !!findAllMediaButton();

  const isSettingsOpen = () => !!findSettingsMenu();

  const getSettingsTriggerPoint = () => {
    const trigger=findSettingsTrigger();
    if(!trigger) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Settings.');
    if(isAgentToggle(trigger) || norm(trigger.textContent)==='agent'){
      throw new Error('AGENT_GUARD: selector Settings trá» nháº§m nÃºt Agent Â· BLOCK CLICK.');
    }
    const point=rectPoint(trigger);
    if(!point) throw new Error('NÃºt Settings khÃ´ng visible.');
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
    if(!trigger) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Settings.');
    if(isAgentToggle(trigger) || norm(trigger.textContent)==='agent'){
      throw new Error('AGENT_GUARD: openSettings trá» nháº§m Agent Â· BLOCK CLICK.');
    }
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

    throw new Error(`KhÃ´ng má»Ÿ Ä‘Æ°á»£c Settings. trigger=${JSON.stringify(diag)}`);
  };

  const findTab = (menu,spec) => [...menu.querySelectorAll('button[role="tab"]')].find(b=>{
    if(spec.suffix) return (b.getAttribute('aria-controls')||'').endsWith(`-content-${spec.suffix}`);
    if(spec.text) return norm(b.textContent)===norm(spec.text);
    return false;
  });

  const selectOption = async (spec,label) => {
    const menu=await openSettings(), tab=findTab(menu,spec);
    if(!tab) throw new Error(`KhÃ´ng tÃ¬m tháº¥y tÃ¹y chá»n ${label}.`);
    if(tab.getAttribute('aria-selected')==='true'||tab.getAttribute('data-state')==='active') return false;
    const x=invoke(tab,['onMouseDown','onPointerDown','onClick']); if(!x) tab.click();
    const ok=await waitFor(()=>{
      const m=findSettingsMenu(), t=m&&findTab(m,spec);
      return t&&(t.getAttribute('aria-selected')==='true'||t.getAttribute('data-state')==='active')?t:null;
    },4000);
    if(!ok) throw new Error(`Flow chÆ°a chá»n Ä‘Æ°á»£c ${label}.`);
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
    if(!closed) throw new Error('KhÃ´ng Ä‘Ã³ng Ä‘Æ°á»£c Settings.');
    return true;
  };

  // SLATE
  const findSlateEl = () => {
    const ph=[...document.querySelectorAll('[data-slate-placeholder="true"]')].find(e=>{
      const t=norm(e.textContent);
      return t.includes('what do you want to create') || t.includes('bạn muốn tạo gì') || t.includes('tạo') || t.includes('create');
    });
    const from=ph?.closest('[data-slate-editor="true"][contenteditable="true"]'); if(from) return from;
    return [...document.querySelectorAll('[data-slate-editor="true"][contenteditable="true"]')].filter(visible).sort((a,b)=>{
      const A=a.getBoundingClientRect(),B=b.getBoundingClientRect(); return B.width*B.height-A.width*A.height;
    })[0] || document.querySelector('[data-slate-editor="true"][contenteditable="true"]') || null;
  };
  const isSlate = v => v&&typeof v==='object'&&Array.isArray(v.children)&&typeof v.apply==='function'&&typeof v.insertText==='function';
  const getSlate = el => {
    const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber$')); if(!fk) throw new Error('Không thấy React Fiber Slate.');
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
    throw new Error('Không tìm thấy Slate editor object.');
  };
  const leaves=(nodes,path=[],out=[])=>{ nodes.forEach((n,i)=>{const p=[...path,i]; if(typeof n?.text==='string') out.push({path:p,text:n.text}); else if(Array.isArray(n?.children)) leaves(n.children,p,out);}); return out; };
  const slateText=e=>leaves(e.children).map(x=>x.text).join('');
  const replacePrompt = async prompt0 => {
    const prompt=String(prompt0??'').trim(); if(!prompt) throw new Error('Prompt đang rỗng.');
    let el=findSlateEl();
    if(!el){
      const allBtn=findAllMediaButton();
      if(allBtn){
        try{ allBtn.click(); await sleep(500); }catch{}
      }
      el=await waitFor(findSlateEl, 4000, 150);
    }
    if(!el) throw new Error('Không tìm thấy ô prompt Slate.');
    const e=getSlate(el); el.focus(); const ls=leaves(e.children); if(!ls.length) throw new Error('Slate không có text leaf.');
    const first=ls[0], last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e)&&typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(120); if(slateText(e)) throw new Error(`Clear prompt thất bại: ${slateText(e)}`);
    e.insertText(prompt); await sleep(450); const final=slateText(e); if(final!==prompt) throw new Error(`Prompt thực tế không đúng: ${final}`);
    return {ok:true,prompt:final};
  };


  // v14.5.6: composer hygiene. Flow keeps reference chips and Slate text between
  // creates, so every IMAGE/VIDEO submit must start from a verified empty composer.
  const clearPrompt = async () => {
    let el=findSlateEl();
    if(!el){
      const allBtn=findAllMediaButton();
      if(allBtn){
        try{ allBtn.click(); await sleep(400); }catch{}
      }
      el=findSlateEl();
    }
    if(!el) return {ok:true,skipped:true,text:''};
    const e=getSlate(el); el.focus();
    const ls=leaves(e.children); if(!ls.length) return {ok:true,text:''};
    const first=ls[0], last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e) && typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(140);
    const remain=slateText(e);
    if(remain) throw new Error(`Clear prompt thất bại: ${remain}`);
    return {ok:true,text:''};
  };

  const composerMediaCards = () => {
    const composerBox = findSlateEl()?.closest('[data-composer],form,div[class*="composer"]') || document.body;
    const candidates = [
      ...document.querySelectorAll('button[data-card-open], div[data-card-open], [data-media-card]'),
      ...composerBox.querySelectorAll('button, div, [role="button"]')
    ];
    return [...new Set(candidates)]
      .filter(visible)
      .filter(b => {
        const hasImg = !!b.querySelector('img');
        const icons = [...b.querySelectorAll('i, mat-icon, svg, [role="button"], span')].map(i => norm(i.textContent || i.getAttribute('aria-label') || ''));
        const hasCancel = icons.some(t => /cancel|close|clear|delete|remove|x/.test(t)) || !!b.querySelector('button[aria-label*="remove" i], button[aria-label*="delete" i], button[aria-label*="clear" i]');
        return hasImg && hasCancel;
      });
  };

  const composerCardInfo = (b,index=0) => {
    const img=b?.querySelector('img')||null;
    const src=img?.getAttribute('src')||null;
    const icons=[...b.querySelectorAll('i, mat-icon, svg')].map(i=>norm(i.textContent || i.getAttribute('aria-label') || ''));
    const hasError=icons.includes('error');
    return {index,...rectPoint(b),src,mediaId:mediaId(src),hasError,error:hasError,ready:!hasError,iconTexts:icons};
  };

  const getComposerMediaState = () => {
    const cards=composerMediaCards();
    const items=cards.map((b,index)=>composerCardInfo(b,index));
    return {count:items.length,validCount:items.filter(x=>!x.hasError).length,errorCount:items.filter(x=>x.hasError).length,items};
  };

  const composerRemoveTarget = card => {
    if(!card) return null;
    const btns = [...card.querySelectorAll('button, [role="button"], i, mat-icon')].filter(visible);
    const cancelBtn = btns.find(b => {
      const txt = norm(b.textContent || b.getAttribute('aria-label') || '');
      return /cancel|close|clear|delete|remove/.test(txt);
    });
    return cancelBtn || card.querySelector('button') || card;
  };

  const getComposerMediaRemovePoint = () => {
    const b=composerMediaCards()[0];
    if(!b) return null;
    const target=composerRemoveTarget(b); const info=composerCardInfo(b,0);
    return {...rectPoint(target),src:info.src,mediaId:info.mediaId,index:0,hasError:info.hasError};
  };

  const removeComposerMediaFirst = async () => {
    const before=composerMediaCards().length;
    const card=composerMediaCards()[0];
    if(!card) return {ok:true,before,after:0,skipped:true};
    const target=composerRemoveTarget(card);
    try{invoke(target,['onClick'],0);}catch{}
    try{dispatchPointerSequence(target);}catch{}
    try{target.click();}catch{}
    try{card.click();}catch{}
    await sleep(250);
    const after=composerMediaCards().length;
    return {ok:after<before,before,after};
  };

  const removeAllComposerMedia = async () => {
    let removed = 0;
    const cards = composerMediaCards();
    for (const card of cards) {
      const target = composerRemoveTarget(card);
      try { target.click(); removed++; } catch {}
    }
    await sleep(250);
    return { ok: true, removed, remaining: composerMediaCards().length };
  };

  const getComposerMediaRemovePointFor = selector => {
    const sel=selector&&typeof selector==='object'?selector:{};
    const rows=composerMediaCards().map((card,index)=>({card,info:composerCardInfo(card,index)}));
    let row=null; const wanted=String(sel.mediaId||'').trim();
    if(wanted) row=rows.find(x=>String(x.info.mediaId||'')===wanted)||null;
    if(!row && Number.isInteger(sel.index)) row=rows.find(x=>x.info.index===Number(sel.index))||null;
    if(!row && sel.errorOnly===true) row=rows.find(x=>x.info.hasError)||null;
    if(!row) return null; if(sel.errorOnly===true&&!row.info.hasError) return null;
    const target=composerRemoveTarget(row.card); return {...rectPoint(target),...row.info};
  };

  // CREATE
  const findCreate = () => [...document.querySelectorAll('button')].filter(visible).find(b=>{
    const icons=[...b.querySelectorAll('i')].map(i=>norm(i.textContent));
    const labels=[...b.querySelectorAll('span')].map(s=>norm(s.textContent));
    const text=norm(b.textContent||'');
    return icons.includes('arrow_forward')&&(labels.includes('create')||labels.includes('t?o')||/t.o/i.test(text));
  });
  const waitCreateReady = async (timeout=15000) => {
    const b=await waitFor(()=>{ const c=findCreate(); return c&&!c.disabled&&c.getAttribute('aria-disabled')!=='true'?c:null; },timeout,200);
    if(!b){const c=findCreate(); throw new Error(`Create váº«n khÃ³a. aria-disabled=${c?.getAttribute('aria-disabled')}`)}
    return true;
  };
  const getCreatePoint = () => {
    const b=findCreate(); if(!b) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Create.');
    if(b.disabled||b.getAttribute('aria-disabled')==='true') throw new Error('NÃºt Create Ä‘ang bá»‹ khÃ³a.');
    const r=b.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2,text:b.textContent};
  };

  // ASSETS
  const findSearch = () => {
    const inputs=[...document.querySelectorAll('input#add-menu-input,input[type="text"]')].filter(visible);
    return inputs.find(i=>/search assets|t.m ki.m th.nh ph.n|t.m ki.m/i.test(String(i.getAttribute('placeholder')||''))) || null;
  };
  const findDialog = () => findSearch()?.closest('[role="dialog"]')||null;
  const findAddMedia = () => [...document.querySelectorAll('button,[role="button"]')]
    .filter(visible)
    .map(b=>({
      b,
      r:b.getBoundingClientRect(),
      label:elementLabel(b),
      text:(b.textContent||'').replace(/\s+/g,' ').trim(),
      icons:[...b.querySelectorAll('i,mat-icon')].map(i=>norm(i.textContent)).filter(Boolean),
      hasPopup:String(b.getAttribute('aria-haspopup')||'')
    }))
    .filter(x=>x.r.width>0 && x.r.height>0)
    .map(x=>{
      const label=norm(`${x.label} ${x.text}`);
      let score=0;
      if(x.hasPopup==='dialog') score+=35;
      if(x.icons.includes('add_2')) score+=90;
      if(x.icons.includes('add')) score+=45;
      if(/add media|upload media|th.m n.i dung nghe nh.n|t.i n.i dung nghe nh.n/.test(label)) score+=80;
      if(/t.o|create/.test(label) && x.icons.includes('add_2')) score+=35;
      if(/tr. gi.p|c.i ..t|kh.c|s.p x.p|l.c|pro|th.ng r.c/.test(label)) score-=120;
      if(x.r.top < 90 && x.icons.includes('add')) score-=40;
      if(x.r.top > Math.max(300, window.innerHeight*0.45)) score+=20;
      return {...x,score};
    })
    .filter(x=>x.score>0)
    .sort((a,b)=>b.score-a.score || b.r.top-a.r.top)[0]?.b||null;

  // v10.2: expose coordinates/state only. Background performs all critical
  // Asset Picker clicks through chrome.debugger trusted input per tab.
  const isAssetPickerOpen = () => !!findSearch();

  const getAddMediaPoint = () => {
    const b=findAddMedia();
    if(!b) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Add media.');
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
    if(!b) throw new Error('Kh??ng t??m th???y n??t Add media.');
    const target=b.querySelector('[data-type="button-overlay"]') || b;
    try{invoke(b,['onClick'],0);}catch{}
    try{dispatchPointerSequence(target);}catch{}
    try{target.click();}catch{}
    try{b.click();}catch{}
    return !!(await waitFor(()=>isAssetPickerOpen(),1500,80));
  };

  // v14: find the visible action that opens Flow's native image file chooser.
  // The actual file is injected by background.js through CDP DOM.setFileInputFiles,
  // so page.js only needs to expose a trustworthy click coordinate when Flow lazily
  // creates the <input type=file> after choosing an Upload/From device action.
  const getUploadImagePoint = () => {
    const d=findDialog();
    if(!d) throw new Error('Asset Picker chÆ°a má»Ÿ khi tÃ¬m Upload Image.');
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
    if(!target) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Upload Image / From device trong Asset Picker.');
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
    if(!tab) throw new Error('KhÃ´ng tÃ¬m tháº¥y tab Images trong Asset Picker.');
    return {
      ...rectPoint(tab),
      selected:tab.getAttribute('aria-selected')==='true',
      text:(tab.textContent||'').replace(/\s+/g,' ').trim()
    };
  };

  const setAssetSearch = async prompt => {
    if(!findSearch()) throw new Error('Asset Picker chÆ°a má»Ÿ khi nháº­p Search assets.');
    await setSearch(prompt);
    return {ok:true,prompt:String(prompt??'').trim()};
  };

  const getAssetOptionPoint = id => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId áº£nh Ä‘ang rá»—ng.');
    const d=findDialog();
    if(!d) throw new Error('Asset Picker chÆ°a má»Ÿ.');
    const option=[...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')]
      .filter(visible)
      .find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)) || null;
    if(!option) return null;
    // Click the role=option card itself, not the IMG. New Flow builds can treat an IMG
    // click as preview/focus while the option card is the actual selection target.
    return {
      ...rectPoint(option),
      mediaId:wanted,
      title:option.querySelector('img')?.getAttribute('alt')||'',
      selected:option.getAttribute('aria-selected')==='true' || option.getAttribute('data-state')==='checked' || option.getAttribute('data-state')==='selected',
      dataIndex:option.closest('[data-index]')?.getAttribute('data-index')||null,
      clickTarget:'role-option'
    };
  };


  const clickAssetOptionByMediaId = async id => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId ?nh ?ang r?ng.');
    const d=findDialog();
    if(!d) throw new Error('Asset Picker ch?a m?.');
    const option=[...d.querySelectorAll('[role="option"],button,[role="button"]')]
      .filter(visible)
      .find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)) || null;
    if(!option) return {ok:false,reason:'not-found'};
    const targets=[option.querySelector('[data-type="button-overlay"]'),option.querySelector('button,[role="button"]'),option].filter(Boolean);
    for(const target of targets){
      try{invoke(target,['onClick'],0);}catch{}
      try{dispatchPointerSequence(target);}catch{}
      try{target.click();}catch{}
      await sleep(180);
    }
    const selected=option.getAttribute('aria-selected')==='true' || option.getAttribute('data-state')==='checked' || option.getAttribute('data-state')==='selected';
    return {ok:true,selected,mediaId:wanted,title:option.querySelector('img')?.getAttribute('alt')||'',state:option.getAttribute('data-state')||'',ariaSelected:option.getAttribute('aria-selected')||''};
  };
  const getAssetPickerCommitPoint = () => {
    const d=findDialog(); if(!d) return null;
    const selected=[...d.querySelectorAll('[role="option"]')].filter(o=>visible(o) && (o.getAttribute('aria-selected')==='true'||o.getAttribute('data-state')==='checked'||o.getAttribute('data-state')==='selected')).length;
    const candidates=[...d.querySelectorAll('button,[role="button"]')]
      .filter(visible)
      .filter(b=>!b.disabled&&b.getAttribute('aria-disabled')!=='true')
      .filter(b=>b.getAttribute('role')!=='tab');
    const positive=['add','insert','use','select','done','attach','choose','continue','th?m','ch?n','xong','s? d?ng','??nh k?m','ti?p t?c','th?m v?o c?u l?nh'];
    const negative=['upload','from device','images','videos','image','video','voice','gi?ng n?i','h?nh ?nh','t?t c?','cancel','close','search','t?i l?n','h?y','??ng'];
    const ranked=candidates.map(b=>{
      const text=norm(`${b.textContent||''} ${b.getAttribute('aria-label')||''} ${iconText(b)}`);
      let score=0;
      if(positive.some(x=>text===x)) score+=14;
      else if(positive.some(x=>text.includes(x))) score+=8;
      if(negative.some(x=>text.includes(x))) score-=20;
      const r=b.getBoundingClientRect();
      if(r.bottom>innerHeight*0.55) score+=2;
      if(r.right>innerWidth*0.55) score+=1;
      return {b,text,score};
    }).filter(x=>x.score>0).sort((a,b)=>b.score-a.score);
    const hit=ranked[0];
    return hit?{...rectPoint(hit.b),text:hit.text,score:hit.score,selectedCount:selected}:null;
  };

  const getAssetPickerSelectionState = id => {
    const wanted=String(id||'').trim();
    const open=!!findSearch();
    const composer=getComposerMediaState();
    if(!open) return {open:false,selected:false,composerCount:Number(composer?.count||0),confirmPoint:null};
    const d=findDialog();
    const option=d?[...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')].filter(visible).find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)):null;
    const selected=!!option&&(option.getAttribute('aria-selected')==='true'||option.getAttribute('data-state')==='checked'||option.getAttribute('data-state')==='selected');
    const confirmPoint=getAssetPickerCommitPoint();
    return {
      open:true,
      selected,
      selectedCount:Number(confirmPoint?.selectedCount||0),
      commitReady:Boolean(confirmPoint && Number(confirmPoint.selectedCount||0)>0),
      composerCount:Number(composer?.count||0),
      confirmPoint
    };
  };

  const openPicker = async () => {
    let input=findSearch(); if(input) return input;
    await closeSettings().catch(()=>{});
    const b=findAddMedia(); if(!b) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Add media.');
    const x=invoke(b,['onClick','onPointerDown','onMouseDown']); if(!x) b.click();
    input=await waitFor(findSearch,6000); if(!input) throw new Error('Asset Picker khÃ´ng má»Ÿ.');
    return input;
  };

  const closePicker = async () => {
    if(!findSearch()) return true;
    const b=findAddMedia();
    if(b&&b.getAttribute('aria-expanded')==='true'){ const x=invoke(b,['onClick','onPointerDown','onMouseDown']); if(!x) b.click(); }
    let closed=await waitFor(()=>!findSearch(),1000); if(closed) return true;
    const active=document.activeElement||document.body, o={key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true,composed:true,cancelable:true};
    active.dispatchEvent(new KeyboardEvent('keydown',o)); document.dispatchEvent(new KeyboardEvent('keydown',o)); document.dispatchEvent(new KeyboardEvent('keyup',o));
    closed=await waitFor(()=>!findSearch(),3000); if(!closed) throw new Error('KhÃ´ng Ä‘Ã³ng Ä‘Æ°á»£c Asset Picker.');
    return true;
  };

  const selectImagesTab = async () => {
    const d=findDialog(); if(!d) throw new Error('Asset Picker chÆ°a má»Ÿ.');
    const tab=[...d.querySelectorAll('nav[role="tablist"] button[role="tab"]')].find(b=>[...b.querySelectorAll('i')].some(i=>norm(i.textContent)==='image'));
    if(!tab) return false; if(tab.getAttribute('aria-selected')==='true') return true;
    const x=invoke(tab,['onMouseDown','onClick','onPointerDown']); if(!x) tab.click();
    const selected=await waitFor(()=>tab.getAttribute('aria-selected')==='true'?tab:null,2500);
    if(!selected) throw new Error('ÄÃ£ click tab Images nhÆ°ng Flow chÆ°a chá»n Images.');
    return true;
  };

  const setSearch = async q => {
    let i=findSearch(); if(!i){await openPicker(); i=findSearch();}
    if(!i) throw new Error('KhÃ´ng tÃ¬m tháº¥y Search assets.');
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set; if(!setter) throw new Error('KhÃ´ng láº¥y Ä‘Æ°á»£c input setter.');
    i.focus(); setter.call(i,''); i.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'deleteContentBackward'})); await sleep(80);
    const text=String(q??'').trim(); setter.call(i,text); i.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'insertText',data:text})); i.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
    await sleep(500); return true;
  };

  // v14.5.23: Flow asset thumbnails do not expose generated media IDs in one stable URL shape.
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
      .map(o=>{
        const img=o.querySelector('img');
        const src=img?.getAttribute('src')||'';
        const srcMediaId=mediaId(src);
        const imagePending=Boolean(img && !img.complete);
        const imageLoaded=Boolean(img && img.complete && Number(img.naturalWidth||0)>0 && Number(img.naturalHeight||0)>0);
        const imageBroken=Boolean(img && img.complete && (Number(img.naturalWidth||0)<=0 || Number(img.naturalHeight||0)<=0));
        return {
          mediaId:srcMediaId,
          srcMediaId,
          title:img?.getAttribute('alt')||'',
          src,
          hasImage:Boolean(img&&src),
          imagePending,
          imageLoaded,
          imageBroken,
          validImage:Boolean(srcMediaId&&src&&imageLoaded),
          dataIndex:o.closest('[data-index]')?.getAttribute('data-index')||null,
          selected:o.getAttribute('aria-selected')==='true'
        };
      });
  };

  const getAssetMediaStatus = id => {
    const wanted=String(id||'').trim();
    const d=findDialog();
    if(!wanted) return {found:false,valid:false,reason:'empty-mediaId'};
    if(!d) return {found:false,valid:false,reason:'picker-closed'};
    const options=[...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')].filter(visible);
    const option=options.find(o=>{
      const img=o.querySelector('img');
      const srcId=mediaId(img?.getAttribute('src')||'');
      return srcId===wanted || optionHasMediaId(o,wanted);
    })||null;
    if(!option) return {found:false,valid:false,reason:'not-found'};
    const img=option.querySelector('img');
    const src=img?.getAttribute('src')||'';
    const srcMediaId=mediaId(src);
    const hasImage=Boolean(img&&src);
    const imagePending=Boolean(img&&!img.complete);
    const imageLoaded=Boolean(img&&img.complete&&Number(img.naturalWidth||0)>0&&Number(img.naturalHeight||0)>0);
    const imageBroken=Boolean(img&&img.complete&&(Number(img.naturalWidth||0)<=0||Number(img.naturalHeight||0)<=0));
    const wrongMediaId=Boolean(hasImage&&srcMediaId&&srcMediaId!==wanted);
    const valid=Boolean(hasImage&&imageLoaded&&srcMediaId===wanted);
    let reason=valid?'ok':(!hasImage?'mediaId-without-image':wrongMediaId?'image-mediaId-mismatch':imageBroken?'broken-image':imagePending?'image-loading':'image-without-exact-mediaId');
    return {
      found:true,valid,reason,wanted,hasImage,imagePending,imageLoaded,imageBroken,
      wrongMediaId,srcMediaId,src,title:img?.getAttribute('alt')||'',
      dataIndex:option.closest('[data-index]')?.getAttribute('data-index')||null
    };
  };

  const snapshotImagesByPrompt = async prompt => {
    await closeSettings().catch(()=>{}); await openPicker(); await selectImagesTab(); await setSearch(prompt); await sleep(800);
    const items=collectImages(), ids=items.map(x=>x.mediaId).filter(Boolean); await closePicker(); return {ids,items};
  };

  const prepareNewImageSearch = async prompt => { await closeSettings().catch(()=>{}); await openPicker(); await selectImagesTab(); await setSearch(prompt); return true; };
  const listSearchedImages = () => collectImages();

  const selectImageByMediaId = async id => {
    const wanted=String(id||'').trim();
    if(!wanted) throw new Error('mediaId áº£nh Ä‘ang rá»—ng.');

    const findOption = () => {
      const d=findDialog(); if(!d) return null;
      return [...d.querySelectorAll('[data-testid="virtuoso-item-list"] [role="option"]')]
        .filter(visible)
        .find(o=>mediaId(o.querySelector('img')?.getAttribute('src'))===wanted || optionHasMediaId(o,wanted)) || null;
    };

    let option=findOption();
    if(!option) throw new Error(`KhÃ´ng tÃ¬m tháº¥y áº£nh mediaId=${wanted}`);

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
      if(!clickCurrent()) throw new Error('áº¢nh biáº¿n máº¥t khá»i danh sÃ¡ch trÆ°á»›c click láº§n 2.');
      closed=await waitFor(()=>!findSearch(),4000);
    }
    if(!closed) throw new Error('ÄÃ£ chá»n áº£nh nhÆ°ng Asset Picker chÆ°a Ä‘Ã³ng.');
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
      throw new Error("ChÆ°a chá»n model.");
    }

    if (upper === "NONE") {
      return { ok: true, changed: false, model: "NONE" };
    }

    if (kind !== "IMAGE" && kind !== "VIDEO") {
      throw new Error(`kind model khÃ´ng há»£p lá»‡: ${kind}`);
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
        `KhÃ´ng tÃ¬m tháº¥y nÃºt chá»n model ${kind === "IMAGE" ? "áº£nh" : "video"}.`
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
        `KhÃ´ng tÃ¬m tháº¥y model "${requested}" trong menu Flow.`
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
    if(!requested) throw new Error('ChÆ°a chá»n model.');
    const menu=findSettingsMenu();
    if(!menu) throw new Error('Settings chÆ°a má»Ÿ khi láº¥y model trigger.');
    const trigger=findNestedModelTrigger(String(kind||'').toUpperCase());
    if(!trigger) throw new Error(`KhÃ´ng tÃ¬m tháº¥y nÃºt chá»n model ${kind}.`);
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
    if(!target) throw new Error(`KhÃ´ng tÃ¬m tháº¥y option model "${requested}".`);
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
    if(!wanted) throw new Error('mediaId áº£nh Ä‘ang rá»—ng.');

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

    throw new Error(`áº¢nh Ä‘Ã£ SUCCESS nhÆ°ng Asset Picker chÆ°a tháº¥y mediaId=${wanted}`);
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
    if(!input) throw new Error('KhÃ´ng tÃ¬m tháº¥y Ã´ Search assets cá»§a All Media.');
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
    const mediaId=mediaIdFromVideoSrc(video?.getAttribute('src')||video?.src||'');
    const href=tile.querySelector('a[href*="/edit/"]')?.getAttribute('href')||null;
    const titleNode=[...tile.querySelectorAll('div')].find(el=>{
      const cls=String(el.className||'');
      return cls.includes('sc-899ba078-3') && norm(el.textContent);
    });
    const title=(titleNode?.textContent||'').replace(/\s+/g,' ').trim() || null;
    return {
      mediaId,
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
    if(!wanted) throw new Error('video mediaId Ä‘ang rá»—ng.');
    return listVisibleVideoTiles().find(x=>x.mediaId===wanted)||null;
  };

  const getVideoTileInfoByTitle = title0 => {
    const wanted=norm(title0);
    if(!wanted) return {match:null,count:0,matches:[]};
    const matches=listVisibleVideoTiles().filter(x=>norm(x.title)===wanted);
    return {match:matches[0]||null,count:matches.length,matches};
  };

  const getAddClipPoint = () => {
    const b=[...document.querySelectorAll('button[data-add-button="true"]')].filter(visible)
      .find(x=>norm(x.textContent).includes('add clip')) || null;
    if(!b) throw new Error('KhÃ´ng tÃ¬m tháº¥y nÃºt Add Clip.');
    return {...rectPoint(b),text:(b.textContent||'').replace(/\s+/g,' ').trim(),expanded:b.getAttribute('aria-expanded'),state:b.getAttribute('data-state')};
  };

  const getExtendMenuPoint = () => {
    const items=[...document.querySelectorAll('[role="menuitem"]')].filter(visible);
    const b=items.find(el=>{
      const t=norm(el.textContent), icons=norm(iconText(el));
      return t.includes('extend') && (icons.includes('keyboard_double_arrow_right') || t.includes('veo 3.1'));
    }) || items.find(el=>norm(el.textContent).includes('extend')) || null;
    if(!b) throw new Error('KhÃ´ng tÃ¬m tháº¥y menu Extend.');
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
    if(!prompt) throw new Error('Prompt kÃ©o dÃ i video Ä‘ang rá»—ng.');
    const el=await waitFor(findExtendSlateEl,8000,120);
    if(!el) throw new Error('KhÃ´ng tÃ¬m tháº¥y Ã´ What happens next?');
    const e=getSlate(el); el.focus(); const ls=leaves(e.children); if(!ls.length) throw new Error('Extend Slate khÃ´ng cÃ³ text leaf.');
    const first=ls[0],last=ls.at(-1);
    e.apply({type:'set_selection',properties:e.selection,newProperties:{anchor:{path:first.path,offset:0},focus:{path:last.path,offset:last.text.length}}});
    if(slateText(e)&&typeof e.deleteFragment==='function') e.deleteFragment();
    await sleep(100);
    if(slateText(e)) throw new Error(`Clear extend prompt tháº¥t báº¡i: ${slateText(e)}`);
    e.insertText(prompt); await sleep(350);
    const final=slateText(e); if(final!==prompt) throw new Error(`Extend prompt thá»±c táº¿ khÃ´ng Ä‘Ãºng: ${final}`);
    return {ok:true,prompt:final};
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
    if(!wanted) throw new Error('mediaId video r?ng.');
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

  const __rawFlowPairAuto={getVersion:()=>FLOW_PAIR_AUTO_VERSION,getProjectInfo,getCreateProjectPoint,getAllMediaPoint,isAllMediaAvailable,isSettingsOpen,getAgentModeState,getSettingsTriggerPoint,openSettings:async()=>{await openSettings();return true;},getModelTriggerPoint,getModelOptionPoint,selectModel,applySettings,verifyStageSettings,ensureStageSettings,closeSettings,replacePrompt,clearPrompt,getComposerMediaState,getComposerMediaRemovePoint,getComposerMediaRemovePointFor,removeComposerMediaFirst,waitCreateReady,getCreatePoint,isAssetPickerOpen,getAddMediaPoint,openAssetPicker,getUploadImagePoint,isImagesTabSelected,getImagesTabPoint,setAssetSearch,getAssetOptionPoint,clickAssetOptionByMediaId,getAssetPickerCommitPoint,getAssetPickerSelectionState,getAssetMediaStatus,snapshotImagesByPrompt,prepareNewImageSearch,listSearchedImages,selectImageByMediaId,waitAndSelectImageByMediaId,closeAssetPicker:closePicker,findGlobalAssetSearch:()=>!!findGlobalAssetSearch(),setGlobalAssetSearch,listVisibleVideoTiles,getVideoTileInfoByMediaId,getVideoTileInfoByTitle,getAddClipPoint,getExtendMenuPoint,isExtendComposerOpen,replaceExtendPrompt,findSignedVideoResource,probeVideoRedirect};
  const __safeWhileAborted = new Set(['getVersion','abortAll','resumeAll','getAbortState']);
  const __wrappedFlowPairAuto={};
  for(const [name,fn] of Object.entries(__rawFlowPairAuto)){
    if(typeof fn!=='function'){__wrappedFlowPairAuto[name]=fn;continue;}
    if(__safeWhileAborted.has(name)){__wrappedFlowPairAuto[name]=fn;continue;}
    __wrappedFlowPairAuto[name]=(...args)=>{assertPageActive();return fn(...args);};
  }
  __wrappedFlowPairAuto.abortAll=abortAll;
  __wrappedFlowPairAuto.resumeAll=resumeAll;
  __wrappedFlowPairAuto.getAbortState=getAbortState;
  window.FlowPairAuto=__wrappedFlowPairAuto;
})();

