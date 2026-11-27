// Экран «Анализ игр» в главном окне: последние 20 игр игрока, разобранные той же моделью, что работает в драфте.
{
const {CH,evaluate,norm,iconId,R1,roleFit}=window.MLK_MODEL;
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const SERVED=location.hostname==='127.0.0.1'&&location.port==='27500';
const BASE=SERVED?'':'http://127.0.0.1:27500';
const RS=['T','J','M','B','S'];
const ROLE_NAME={T:'Топ',J:'Лес',M:'Мид',B:'Бот',S:'Саппорт'};
const QUEUES={420:'Ранговая Solo/Duo',440:'Ранговая Flex',400:'Обычная (драфт)',430:'Обычная (вслепую)',490:'Быстрая игра',
  480:'Быстрая игра',450:'ARAM',1700:'Арена',1710:'Арена',900:'URF',1900:'URF',0:'Пользовательская игра'};
const SKIP={mode:'Не Ущелье призывателей — модель здесь не применяется',remake:'Ремейк — не учитывается',
  partial:'Клиент не отдал полный состав этой игры',unknown:'Этого чемпиона пока нет в модели'};
const TIERS=[['S+','Sp'],['S','S'],['A','A'],['B','B'],['C','C'],['D','D']];

/* Чемпион по номеру из клиента: псевдоним Riot → запись модели */
const BY_ALIAS={};CH.forEach(c=>{BY_ALIAS[norm(iconId(c.n))]=c});
const CHAMP=new Map(Object.entries(GAME_DATA.champions).map(([alias,[id]])=>[id,{alias,c:BY_ALIAS[norm(alias)]||null}]));
const champ=id=>CHAMP.get(id)||{alias:'',c:null};
const champName=id=>{const x=champ(id);return x.c?.n||x.alias||tr('Чемпион')};
const portrait=(id,cls='')=>`<span class="port ${cls}"><span class="portrait ${champ(id).alias?'icon-'+champ(id).alias:''}" aria-hidden="true"></span></span>`;
const portraitByName=(n,cls='')=>`<span class="port ${cls}"><span class="portrait icon-${iconId(n)}" aria-hidden="true"></span></span>`;
const sgn=v=>(v>0?'+':v<0?'−':'')+Math.abs(R1(v));
const cls=v=>v>0?'pos':v<0?'neg':'';
const pct=(a,b)=>b?Math.round(a/b*100)+'%':'—';

/* Веса и пул читаются при каждом разборе: их можно поменять на экране драфта. */
let W={l:1,c:1,s:1},POOL=new Set();
function readPrefs(){
  let prefs={};
  try{prefs=JSON.parse(localStorage.getItem('draft-helper'))||{}}catch(e){}
  W={l:1,c:1,s:1,...prefs.w};
  POOL=new Set(String(prefs.pool||'').split(/[,\n]/).map(x=>BY_ALIAS[norm(iconId(x.trim()))]||CH.find(c=>norm(c.n)===norm(x.trim()))).filter(Boolean));
}

/* Роли: клиент не говорит, кто на какой линии. Перебираем все раздачи ролей пятерым (120 вариантов)
   и берём ту, где лучше всего сходятся подсказка клиента, Смайт и то, как часто чемпиона играют на линии (role-data.js). */
function assignRoles(team){
  const fit=(p,r)=>{
    let s=roleFit(p.c,r);
    if(p.smite)s+=r==='J'?3:-2;
    if(p.hint)s+=p.hint===r?1.5:p.hint.includes(r)?.7:0;
    return s;
  };
  let best=null,bestS=-Infinity;
  const go=(i,used,acc,s)=>{
    if(i===team.length){if(s>bestS){bestS=s;best=acc.slice()}return}
    for(const r of RS){if(used.has(r))continue;used.add(r);acc.push(r);go(i+1,used,acc,s+fit(team[i],r));acc.pop();used.delete(r)}
  };
  go(0,new Set(),[],0);
  team.forEach((p,i)=>{p.role=best[i]});
  team.sort((a,b)=>RS.indexOf(a.role)-RS.indexOf(b.role));
}

function tierOf(f,t){
  let k=f<.05?0:f<.15?1:f<.35?2:f<.65?3:f<.85?4:5;
  if(t<1)k=Math.max(k,3);if(t<=0)k=Math.max(k,4);
  return TIERS[k];
}

/* Разбор одной игры моделью */
function analyse(g){
  const a={g,id:String(g.id)};
  const ps=(g.players||[]).map(p=>({...p,c:champ(p.champ).c}));
  a.me=ps.find(p=>p.me)||null;
  if(g.map!==11||(g.mode&&g.mode!=='CLASSIC')){a.skip='mode';return a}
  if(g.remake){a.skip='remake';return a}
  const ally=ps.filter(p=>p.ally),enemy=ps.filter(p=>!p.ally);
  if(!a.me||ally.length!==5||enemy.length!==5){a.skip='partial';return a}
  assignRoles(ally);assignRoles(enemy);
  Object.assign(a,{ally,enemy,role:a.me.role});
  if(!a.me.c){a.skip='unknown';return a}
  const bans=[...(g.bans?.ally||[]),...(g.bans?.enemy||[])].map(id=>champ(id).c?.n).filter(Boolean).join(', ');
  const side=t=>{const o={T:'',J:'',M:'',B:'',S:''};t.forEach(p=>{if(p.c)o[p.role]=p.c.n});return o};
  const state=(team,other,r)=>({me:r,ally:side(team),enemy:side(other),bans,pool:'',poolOnly:false,w:W});
  const all=evaluate(state(ally,enemy,a.role));
  const listed=all.find(x=>x.c===a.me.c);
  a.mine=listed||evaluate(state(ally,enemy,a.role),a.me.c)[0];
  a.rank=all.filter(x=>x.t>a.mine.t+1e-9).length+1;
  a.of=all.length+(listed?0:1);
  a.tier=tierOf((a.rank-1)/a.of,a.mine.t);
  a.alts=all.filter(x=>x.c!==a.me.c).slice(0,3);
  a.poolBest=POOL.size?all.find(x=>POOL.has(x.c)&&x.c!==a.me.c)||null:null;
  const team=(t,o)=>t.reduce((sum,p)=>{if(!p.c)return sum;p.eval=evaluate(state(t,o,p.role),p.c)[0];return sum+p.eval.t},0);
  a.allyScore=team(ally,enemy);a.enemyScore=team(enemy,ally);
  a.diff=a.allyScore-a.enemyScore;
  return a;
}

/* ---------- состояние окна ---------- */
let data=null,list=[],selected='';

function wins(arr){return arr.filter(a=>a.g.win===true).length}
function rateLine(label,arr){
  return `<div class="ga-rate"><span>${esc(tr(label))}</span><strong>${pct(wins(arr),arr.length)}</strong><small>${arr.length} ${esc(tr(gamesWord(arr.length)))}</small></div>`;
}
function gamesWord(n){
  if(interfaceLanguage==='en')return n===1?'игра':'игр';
  const f=new Intl.PluralRules('ru').select(n);
  return f==='one'?'игра':f==='few'?'игры':'игр';
}

function renderSummary(){
  const done=list.filter(a=>!a.skip),n=done.length,w=wins(done);
  const skipped=list.length-n;
  const box=$('gaSummary');
  box.hidden=!list.length;$('gaInsights').hidden=!n;$('gaChartBox').hidden=!n;
  if(!list.length)return;
  if(!n){box.innerHTML=`<div class="ga-card"><small>${esc(tr('Игр разобрано'))}</small><strong>0</strong><small>${esc(tr('Среди последних игр нет обычных игр на Ущелье призывателей'))}</small></div>`;return}
  const avg=done.reduce((s,a)=>s+a.mine.t,0)/n;
  const avgPlace=done.reduce((s,a)=>s+(a.rank-1)/a.of,0)/n;
  const good=done.filter(a=>a.mine.t>0),bad=done.filter(a=>a.mine.t<=0);
  const ahead=done.filter(a=>a.diff>0),behind=done.filter(a=>a.diff<0);
  const laneLost=done.filter(a=>a.mine.l.v<0);
  box.innerHTML=`
    <div class="ga-card"><small>${esc(tr('Игр разобрано'))}</small><strong>${n}</strong>
      <small>${esc(tr(`Победы: ${w} · Поражения: ${n-w}`+(skipped?` · Пропущено: ${skipped}`:'')))}</small></div>
    <div class="ga-card"><small>${esc(tr('Средняя оценка пика'))}</small><strong class="${cls(avg)}">${sgn(avg)}</strong>
      <small>${esc(tr(`Лучше, чем ${Math.round((1-avgPlace)*100)}% вариантов для вашей роли`))}</small></div>
    <div class="ga-card">${rateLine('Пик в плюсе по модели',good)}${rateLine('Пик в минусе',bad)}</div>
    <div class="ga-card">${rateLine('Драфт в вашу пользу',ahead)}${rateLine('Драфт против вас',behind)}</div>
    <div class="ga-card">${rateLine('Линия проиграна по модели',laneLost)}${rateLine('Пик в топ-3 модели',done.filter(a=>a.rank<=3))}</div>`;
  renderInsights(done,{good,bad,ahead,behind,laneLost});
}

function top(counter,k=3){return Object.entries(counter).sort((a,b)=>b[1]-a[1]).slice(0,k)}
function renderInsights(done,g){
  const n=done.length,notes=[];
  const listNames=arr=>arr.map(([name,c])=>`${name} (${c})`).join(', ');
  if(g.laneLost.length/n>=.3){
    const pairs={};
    for(const a of g.laneLost){const o=a.enemy.find(p=>p.role===a.role);if(o)pairs[a.me.c.n+' — '+(o.c?.n||champName(o.champ))]=(pairs[a.me.c.n+' — '+(o.c?.n||champName(o.champ))]||0)+1}
    notes.push(['neg',tr(`Часто проигранная линия: ${g.laneLost.length} из ${n}`),tr('Модель считала, что оппонент по линии сильнее вашего чемпиона.')+(Object.keys(pairs).length?' '+listNames(top(pairs)):'')]);
  }
  const weakSyn=done.filter(a=>a.mine.s.v<0);
  if(weakSyn.length/n>=.25)notes.push(['neg',tr(`Слабая синергия с командой: ${weakSyn.length} из ${n}`),tr('Пик плохо дополнял состав союзников: урон, фронтлайн или инициация.')]);
  const alts={};
  for(const a of done)if(a.rank>3)for(const x of a.alts)alts[x.c.n]=(alts[x.c.n]||0)+1;
  if(Object.keys(alts).length)notes.push(['',tr('Чаще всего модель предлагала'),listNames(top(alts,5))]);
  const pool={};
  for(const a of done)if(a.poolBest&&a.poolBest.t>=a.mine.t+1)pool[a.poolBest.c.n]=(pool[a.poolBest.c.n]||0)+1;
  if(Object.keys(pool).length)notes.push(['',tr('Из вашего пула лучше подходили'),listNames(top(pool,4))]);
  const mine={};
  for(const a of done){const k=a.me.c.n;(mine[k]=mine[k]||{n:0,w:0,t:0});mine[k].n++;mine[k].t+=a.mine.t;if(a.g.win)mine[k].w++}
  const best=Object.entries(mine).filter(([,v])=>v.n>=2).sort((a,b)=>b[1].t/b[1].n-a[1].t/a[1].n);
  if(best.length)notes.push(['',tr('Ваши чемпионы по средней оценке'),best.slice(0,4).map(([k,v])=>`${k} ${sgn(v.t/v.n)} (${v.w}/${v.n})`).join(', ')]);
  if(g.good.length&&g.bad.length){
    const d=Math.round((wins(g.good)/g.good.length-wins(g.bad)/g.bad.length)*100);
    notes.push(['',tr('Проверка модели на ваших играх'),tr(d>0?`С пиком в плюсе вы выигрывали чаще на ${d} п.п.`:d<0?`С пиком в плюсе вы выигрывали реже на ${-d} п.п.`:'Разницы в доле побед нет.')+' '+tr('На 20 играх разница меньше 20 п.п. — скорее случайность.')]);
  }
  $('gaInsights').innerHTML=`<h2 id="gaInsightsTitle">${esc(tr('Выводы'))}</h2><ul class="ga-notes-list">${notes.map(([c,h,t])=>`<li class="${c}"><b>${esc(h)}</b><span>${esc(t)}</span></li>`).join('')}</ul>`;
}

function renderChart(){
  const done=list.filter(a=>!a.skip).slice().reverse();/* слева направо: от старых к новым */
  const max=Math.max(1,...done.map(a=>Math.abs(a.mine.t)));
  $('gaChart').innerHTML=done.map(a=>{
    const h=Math.max(3,Math.abs(a.mine.t)/max*50);
    const tip=`${a.me.c.n} · ${tr(a.g.win?'Победа':'Поражение')} · ${sgn(a.mine.t)}`;
    return `<button type="button" class="ga-bar ${a.g.win?'w':'l'}${a.id===selected?' on':''}" data-id="${esc(a.id)}" title="${esc(tip)}" aria-label="${esc(tip)}">
      <span class="ga-track"><i style="height:${h}%;${a.mine.t>=0?'bottom:50%':'top:50%'}"></i></span>${portrait(a.me.champ,'xs')}</button>`;
  }).join('');
}

function dateText(ms){return ms>0?new Date(ms).toLocaleDateString(interfaceLanguage,{day:'numeric',month:'short'}):''}
function timeText(s){return s>0?Math.floor(s/60)+':'+String(s%60).padStart(2,'0'):''}
function kdaText(k){return (k||[]).map(x=>Number.isInteger(x)?x:'—').join('/')}

function renderList(){
  $('gaList').innerHTML=list.map(a=>{
    const g=a.g,me=a.me,res=g.remake?'Ремейк':g.win===true?'Победа':g.win===false?'Поражение':'—';
    const score=a.skip?`<span class="ga-skip">${esc(tr(a.skip==='mode'?(QUEUES[g.queue]||'Другой режим'):'Без оценки'))}</span>`
      :`<span class="tier t-${a.tier[1]}">${a.tier[0]}</span><strong class="${cls(a.mine.t)}">${sgn(a.mine.t)}</strong>`;
    return `<li><button type="button" class="ga-row${a.id===selected?' on':''}${a.skip?' skipped':''}" data-id="${esc(a.id)}" data-result="${g.remake?'':g.win?'win':'loss'}" aria-current="${a.id===selected}">
      ${me?portrait(me.champ,'s'):''}<span class="ga-row-main"><b>${esc(tr(res))}</b><small>${esc(me?champName(me.champ):'—')}${a.role?' · '+esc(tr(ROLE_NAME[a.role])):''} · ${kdaText(me?.kda)}</small></span>
      <span class="ga-row-side">${score}<small>${esc(dateText(g.created))}</small></span></button></li>`;
  }).join('');
}

function reasons(x){
  const li=arr=>arr.length?arr.map(([p,t])=>`<li><span class="${cls(p)}">${p?sgn(p):''}</span><span>${esc(tr(t))}</span></li>`).join(''):`<li><span></span><span>${esc(tr('ничего особенного'))}</span></li>`;
  return `<div class="why"><div><h4>${esc(tr('Линия'))}</h4><ul>${li(x.l.why)}</ul></div><div><h4>${esc(tr('Против состава'))}</h4><ul>${li(x.k.why)}</ul></div><div><h4>${esc(tr('Синергия'))}</h4><ul>${li(x.s.why)}</ul></div></div>`;
}
function bars(x){
  const m=Math.max(1,Math.abs(x.l.v),Math.abs(x.k.v),Math.abs(x.s.v));
  const bar=(lab,v)=>{const w=Math.abs(v)/m*50;return `<div class="bar"><span>${esc(tr(lab))}</span><div class="track"><div class="fill" style="width:${w}%;left:${v>=0?50:50-w}%;background:${v>=0?'var(--good)':'var(--bad)'}"></div></div><span class="num">${sgn(v)}</span></div>`};
  return `<div class="bars">${bar('Линия',x.l.v)}${bar('Против состава',x.k.v)}${bar('Синергия',x.s.v)}</div>`;
}

function renderDetail(){
  const a=list.find(x=>x.id===selected),box=$('gaDetail');
  if(!a){box.innerHTML=`<p class="sub">${esc(tr('Выберите игру слева.'))}</p>`;return}
  const g=a.g,me=a.me;
  const res=g.remake?'Ремейк':g.win===true?'Победа':g.win===false?'Поражение':'—';
  const meta=[QUEUES[g.queue]?tr(QUEUES[g.queue]):'',dateText(g.created),timeText(g.duration),me?'KDA '+kdaText(me.kda):''].filter(Boolean);
  let html=`<div class="ga-detail-head">${me?portrait(me.champ):''}<div><div class="kicker">${esc(meta.join(' · '))}</div>
    <div class="big"><span class="${g.win?'pos':g.win===false?'neg':''}">${esc(tr(res))}</span> · ${esc(me?champName(me.champ):'—')}${a.role?' · '+esc(tr(ROLE_NAME[a.role])):''}</div></div>
    ${a.skip?'':`<div class="total"><div class="kicker">${esc(tr('Оценка пика'))}</div><div class="big ${cls(a.mine.t)}">${sgn(a.mine.t)}</div></div>`}</div>`;
  if(a.skip){box.innerHTML=html+`<p class="sub ga-skipnote">${esc(tr(SKIP[a.skip]))}</p>`;return}
  const placeText=tr(a.rank===1?'Лучший вариант по модели':`${a.rank} место из ${a.of}`);
  html+=`<div class="ga-verdict"><span class="tier t-${a.tier[1]}">${a.tier[0]}</span><span>${esc(placeText)}</span>
    <span class="ga-dot">·</span><span>${esc(tr('Драфт команд'))}: <b class="${cls(a.diff)}">${sgn(a.diff)}</b> ${esc(tr(a.diff>1?'в вашу пользу':a.diff<-1?'против вас':'примерно равный'))}</span></div>`;
  html+=bars(a.mine)+reasons(a.mine);
  html+=`<h3 class="ga-sub">${esc(tr(a.rank===1?'Другие сильные варианты':'Модель предложила бы'))}</h3><div class="ga-alts">${a.alts.map(x=>{
    const why=[...x.l.why,...x.k.why,...x.s.why].filter(([p])=>p>0).sort((p,q)=>q[0]-p[0])[0];
    return `<div class="ga-alt">${portraitByName(x.c.n,'s')}<div><b>${esc(x.c.n)}${POOL.has(x.c)?' <em>'+esc(tr('в пуле'))+'</em>':''}</b><small>${why?esc(tr(why[1])):''}</small></div><strong class="${cls(x.t)}">${sgn(x.t)}</strong></div>`;
  }).join('')}</div>`;
  if(a.poolBest&&!a.alts.includes(a.poolBest)&&a.poolBest.t>a.mine.t)html+=`<p class="sub">${esc(tr('Лучший вариант из вашего пула'))}: <b>${esc(a.poolBest.c.n)}</b> <span class="${cls(a.poolBest.t)}">${sgn(a.poolBest.t)}</span></p>`;
  const cell=p=>p?`<div class="ga-pick${p.me?' me':''}">${portrait(p.champ,'s')}<span>${esc(champName(p.champ))}${p.me?' <em>'+esc(tr('вы'))+'</em>':''}</span><b class="${p.eval?cls(p.eval.t):''}">${p.eval?sgn(p.eval.t):'—'}</b></div>`:'<div class="ga-pick">—</div>';
  html+=`<h3 class="ga-sub">${esc(tr('Составы'))}</h3><div class="ga-draft"><div class="ga-draft-head"><span></span><span>${esc(tr('Ваша команда'))} <b class="${cls(a.allyScore)}">${sgn(a.allyScore)}</b></span><span>${esc(tr('Соперники'))} <b class="${cls(a.enemyScore)}">${sgn(a.enemyScore)}</b></span></div>
    ${RS.map(r=>`<div class="ga-draft-row"><span class="ga-role">${esc(tr(ROLE_NAME[r]))}</span>${cell(a.ally.find(p=>p.role===r))}${cell(a.enemy.find(p=>p.role===r))}</div>`).join('')}</div>`;
  const banIcons=ids=>ids.length?ids.map(id=>portrait(id,'xs')).join(''):'—';
  html+=`<div class="ga-bans"><span>${esc(tr('Баны'))}:</span><span>${banIcons(g.bans?.ally||[])}</span><span class="ga-dot">|</span><span>${banIcons(g.bans?.enemy||[])}</span></div>`;
  box.innerHTML=html;
}

function render(){
  renderSummary();renderChart();renderList();renderDetail();
  const view=$('gamesView');localize(view.querySelector('.ga-head'));localize(view.querySelector('.ga-notes'));localize($('gaChartBox').querySelector('.heading'));localize($('gaListTitle'));
}

function select(id,scroll){
  if(!list.some(a=>a.id===id))id=list.find(a=>!a.skip)?.id||list[0]?.id||'';
  selected=id;
  render();
  if(scroll&&id)$('gaDetail').scrollIntoView({block:'nearest'});
}

function status(text){const s=$('gaStatus');s.hidden=!text;s.textContent=text?tr(text):''}

let loading=null;
async function load(){
  loading?.abort();
  const req=new AbortController();loading=req;
  status('Загружаю последние игры из клиента…');$('reload').disabled=true;
  try{
    readPrefs();
    const r=await fetch(BASE+'/games',{cache:'no-store',signal:AbortSignal.any?AbortSignal.any([req.signal,AbortSignal.timeout(90000)]):req.signal});
    if(!r.ok)throw Error('http');
    data=await r.json();
    if(loading!==req)return;
    if(!data){status('Войдите в клиент League of Legends: история игр берётся из него.');list=[];}
    else if(!Array.isArray(data.games)){status('История матчей недоступна');list=[];}
    else{
      list=data.games.map(analyse);
      status(list.length?'':'Пока нет матчей');
      if(data.name)$('gaTitle').textContent=data.name+(data.tag?' #'+data.tag:'');
      const k=list.length;$('gaKicker').textContent=tr('Последние игры')+': '+k;
    }
  }catch(e){
    if(loading!==req)return;
    status(SERVED?'Не удалось получить игры из клиента. Нажмите «Загрузить заново».':'Нет связи с MLK.gg');list=[];
  }finally{
    if(loading===req){loading=null;$('reload').disabled=false}
  }
  $('gaLayout').hidden=!list.length;
  select(wanted||selected,!!wanted);wanted='';
}

/* ---------- переключение экранов ---------- */
let wanted='',loadedAt=0;
$('gamesView').addEventListener('click',e=>{
  const pick=e.target.closest('[data-id]');
  if(pick)select(pick.dataset.id,pick.classList.contains('ga-bar'));
});
$('reload').onclick=load;
/* Показать экран анализа (можно сразу с нужной игрой). Игры перечитываются, если прошло больше минуты. */
function show(id){
  wanted=/^\d+$/.test(String(id||''))?String(id):'';
  if(!loading&&(!data||Date.now()-loadedAt>60000)){loadedAt=Date.now();load();return}
  if(loading)return;
  if(Array.isArray(data?.games)){readPrefs();list=data.games.map(analyse)} /* пул или веса могли поменяться на экране драфта */
  if(wanted){select(wanted,true);wanted=''}else render();
}
window.MLK_GAMES={show,rerender:()=>{if(data?.games)$('gaKicker').textContent=tr('Последние игры')+': '+list.length;if(!$('gamesView').hidden)render()}};
}
