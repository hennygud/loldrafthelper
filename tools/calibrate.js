// Проверка правил модели на реальных играх.
// Для каждого игрока каждой игры считаем, какие правила модели сработали бы для его пика при итоговых составах,
// и сравниваем исход с ожидаемым для этого чемпиона на этой роли. Так видно, работает ли правило
// само по себе, а не потому, что его чаще получают сильные чемпионы.
//
// Запуск:  node tools/calibrate.js matches.jsonl stats.json
// Скрипт дописывает в stats.json раздел "rules": {id: {n, obs, se, z, pred, k}}.
//   n    — в скольких командах правило сработало (игры одной команды считаются вместе: у них общий исход);
//   obs  — насколько чаще (в п.п.) выигрывали, когда правило срабатывало, сверх ожидаемого для чемпиона;
//   pred — сколько п.п. обещает мой балл (балл / 0,6);
//   k    — множитель веса: правило × k. Мой балл — ожидание, данные его уточняют; если данные уверенно
//          (z ≥ 2,5) говорят обратное, k = 0. Правила с k < 0,15 приложение не показывает.
const fs=require('fs'),path=require('path');
const [,,matchesPath,statsPath]=process.argv;
if(!matchesPath||!statsPath){console.error('node tools/calibrate.js matches.jsonl stats.json');process.exit(1)}
const m=require(path.join(__dirname,'..','assets','js','draft.js'));
const stats=JSON.parse(fs.readFileSync(statsPath,'utf8'));
const PT=.6,POS={TOP:'T',JUNGLE:'J',MIDDLE:'M',BOTTOM:'B',UTILITY:'S'};
const BY={};for(const c of m.CH){BY[m.norm(m.iconId(c.n))]=c;BY[m.norm(c.n)]=c}
const champ=n=>BY[m.norm(n)];
// ожидаемый исход: винрейт чемпиона на роли, прижатый к 50% так же, как в «силе на патче»
const base={};
for(const[n,rr]of Object.entries(stats.champs||{}))for(const[r,v]of Object.entries(rr)){
  const se2=2500/Math.max(1,v.games),tau2=1.96;base[m.norm(n)+'|'+r]=50+(v.wr-50)*tau2/(tau2+se2)}
// правила проверяются в чистом виде, без данных о парах
m.D.on=false;
const acc={};   // id -> {clusters: Map(key -> [sumResid, count]), pred sum, count}
let games=0,used=0;
for(const line of fs.readFileSync(matchesPath,'utf8').split('\n')){
  if(!line.trim())continue;let g;try{g=JSON.parse(line)}catch{continue}
  if(stats.patch&&g.patch!==stats.patch)continue;
  games++;
  const team={100:{},200:{}};let ok=true;
  for(const[n,pos,t,w]of g.p){const r=POS[pos],c=champ(n);if(!r||!c||!team[t]){ok=false;break}team[t][r]={c,w,n}}
  if(!ok||Object.keys(team[100]).length!==5||Object.keys(team[200]).length!==5)continue;
  used++;
  for(const t of [100,200]){
    const mine=team[t],theirs=team[t===100?200:100];
    const en={};for(const[r,x]of Object.entries(theirs))en[r]=x.c;const E=Object.values(en);
    for(const[r,x]of Object.entries(mine)){
      const A=Object.entries(mine).filter(([rr])=>rr!==r).map(([,y])=>y.c);
      const resid=x.w*100-(base[m.norm(x.n)+'|'+r]??50);
      const fired=[...m.lane(x.c,r,en).why,...m.comp(x.c,E,en[r],r).why,...m.syn(x.c,A).why];
      for(const[p,,id]of fired){if(!id||!p)continue;
        const a=acc[id]??={cl:new Map(),pred:0,cnt:0};
        const key=g.id+'|'+t,cur=a.cl.get(key)||[0,0];cur[0]+=resid;cur[1]++;a.cl.set(key,cur);
        a.pred+=p/PT;a.cnt++}
    }
  }
}
const rules={};
for(const[id,a]of Object.entries(acc)){
  // среднее отклонение по срабатываниям и кластерная ошибка (одна команда = один кластер)
  let sum=0;for(const[s]of a.cl.values())sum+=s;
  const obs=sum/a.cnt;let v=0;for(const[s,c]of a.cl.values())v+=(s-c*obs)**2;
  const se=Math.sqrt(v)/a.cnt,pred=a.pred/a.cnt,z=Math.abs(obs)/se;
  // мой балл как ожидание с разбросом в его же размер; данные с ошибкой se
  const tau=Math.max(.5,Math.abs(pred));
  const pm=(pred/tau**2+obs/se**2)/(1/tau**2+1/se**2);
  let k=Math.max(0,Math.min(1.5,pm/pred));
  if(Math.sign(obs)!==Math.sign(pred)&&z>=2.5)k=0;
  rules[id]={n:a.cl.size,obs:+obs.toFixed(2),se:+se.toFixed(2),z:+z.toFixed(2),pred:+pred.toFixed(2),k:+k.toFixed(2)};
}
stats.rules=rules;stats.rules_games=used;
fs.writeFileSync(statsPath,JSON.stringify(stats));
console.log(`игр патча ${stats.patch}: ${games}, с полными составами: ${used}`);
console.log('правило'.padEnd(24),'команд'.padStart(7),'обещал'.padStart(7),'данные'.padStart(7),'±'.padStart(5),'вес');
for(const[id,r]of Object.entries(rules).sort((a,b)=>a[0].localeCompare(b[0])))
  console.log(id.padEnd(24),String(r.n).padStart(7),(r.pred>0?'+':'')+r.pred.toFixed(1).padStart(6),(r.obs>0?'+':'')+r.obs.toFixed(1).padStart(6),r.se.toFixed(1).padStart(5),'×'+r.k.toFixed(2)+(r.k<.15?'  снято':r.z>=2.5&&Math.sign(r.obs)===Math.sign(r.pred)?'  подтверждено':''));
