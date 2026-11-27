// The season totals come from ranked stats. Match history is a separate, recent sample.
function createProfileBanner(base, displayName, esc) {
  const panel = document.getElementById('playerProfile');
  const banner = panel.closest('.banner');
  const champions = new Map(Object.entries(GAME_DATA.champions).map(([alias, [id]]) => [id, alias]));
  const tiers = {IRON:'Железо', BRONZE:'Бронза', SILVER:'Серебро', GOLD:'Золото',
    PLATINUM:'Платина', EMERALD:'Изумруд', DIAMOND:'Алмаз', MASTER:'Мастер',
    GRANDMASTER:'Грандмастер', CHALLENGER:'Претендент'};
  let profile = null, phase = null, request = null, refreshAt = 0;
  let expanded = false, rendered = '';

  function countLabel(value, win) {
    const forms = win ? ['победа','победы','побед'] : ['поражение','поражения','поражений'];
    const plural = new Intl.PluralRules(interfaceLanguage).select(value);
    const word = interfaceLanguage === 'en'
      ? (win ? (value === 1 ? 'win' : 'wins') : (value === 1 ? 'loss' : 'losses'))
      : forms[plural === 'one' ? 0 : plural === 'few' ? 1 : 2];
    return value + ' ' + word;
  }
  const count = n => Number.isInteger(n) && n >= 0;

  function matchCard(match) {
    const alias = champions.get(match.champion);
    const name = alias ? displayName(alias) : tr('Чемпион');
    const outcome = {win:'Победа', loss:'Поражение', remake:'Переигровка'}[match.result];
    const kda = match.kda.map(n => count(n) ? n : '—').join('/');
    const details = [];
    if (expanded && match.timestamp > 0) {
      details.push(new Date(match.timestamp).toLocaleDateString(interfaceLanguage, {day:'numeric', month:'short', year:'numeric'}));
    }
    if (expanded && count(match.duration)) {
      details.push(Math.floor(match.duration/60) + ':' + String(match.duration%60).padStart(2,'0'));
    }
    return `<li class="profile-match" data-result="${outcome ? match.result : ''}">
      <i class="portrait ${alias ? 'icon-'+alias : ''}" aria-hidden="true"></i>
      <div><b>${outcome ? tr(outcome) : '—'}</b><small>${esc(name)} · ${kda}</small>
      ${details.length ? '<small>'+esc(details.join(' · '))+'</small>' : ''}</div></li>`;
  }

  function render() {
    const key = JSON.stringify([profile, interfaceLanguage, expanded]);
    if (key === rendered) return;
    rendered = key;
    panel.hidden = !profile;
    banner.querySelector('.banner-copy').hidden = !!profile;
    banner.classList.toggle('has-profile', !!profile);
    banner.setAttribute('aria-labelledby', profile ? 'profileName' : 'phaseTitle');
    if (!profile) { panel.replaceChildren(); return; }

    const {name, tag, level, icon, rank, matches} = profile;
    const tier = rank?.tier;
    let rankText = '—';
    if (rank) rankText = tier && tier !== 'NONE' && tier !== 'UNRANKED'
      ? esc(tr(tiers[tier] || tier)) : tr('Без ранга');
    if (tiers[tier] && !['MASTER','GRANDMASTER','CHALLENGER'].includes(tier) && rank.division && rank.division !== 'NA') {
      rankText += ' ' + esc(rank.division);
    }
    const record = count(rank?.wins) && count(rank?.losses);
    const total = record ? rank.wins + rank.losses : 0;
    const rate = total ? new Intl.NumberFormat(interfaceLanguage, {style:'percent', maximumFractionDigits:1}).format(rank.wins/total) : '—';
    const history = matches?.length ? matches.slice(0, expanded ? 10 : 5).map(matchCard).join('') : '';
    const focused = document.activeElement?.id === 'profileMore';
    panel.innerHTML = `<div class="profile-id"><div class="profile-avatar">
      <span aria-hidden="true">${esc(Array.from(name)[0] || '·')}</span>${count(icon) ? '<img src="'+base+'/profile-icon/'+icon+'.jpg" alt="">' : ''}</div>
      <div><small>${tr('Профиль игрока')}</small><h1 id="profileName">${esc(name)} ${tag ? '<em>#'+esc(tag)+'</em>' : ''}</h1>
      ${count(level) ? '<small>'+tr('Уровень')+' '+level+'</small>' : ''}</div></div>
      <div class="profile-numbers"><div><small>${tr('Ранг')} · Solo/Duo</small><strong>${rankText}
      ${tiers[tier] && count(rank.leaguePoints) ? '<em>'+rank.leaguePoints+' LP</em>' : ''}</strong></div>
      <div><small>${tr('Текущий сезон')} · Solo/Duo</small><strong>${record ? countLabel(rank.wins,true)+' <em>'+countLabel(rank.losses,false)+'</em>' : tr('Нет данных')}</strong></div>
      <div><small>${tr('Доля побед за сезон')}</small><strong>${rate}</strong></div></div>
      <div class="profile-history"><div class="profile-history-head"><h3>${tr('Последние матчи')}</h3>
      ${history ? '<button id="profileMore" aria-controls="recentGames" aria-expanded="'+expanded+'">'+
        (expanded ? tr('Свернуть историю') : matches.length > 5 ? tr('Все матчи')+' ('+matches.length+') →' : tr('Подробнее')+' →')+'</button>' : ''}</div>
      ${history ? '<ol id="recentGames">'+history+'</ol>' : '<small>'+tr(matches ? 'Пока нет матчей' : 'История матчей недоступна')+'</small>'}</div>`;
    panel.querySelector('.profile-avatar img')?.addEventListener('error', e => { e.target.hidden = true; });
    if (focused) document.getElementById('profileMore')?.focus({preventScroll:true});
  }
  panel.addEventListener('click', e => {
    if (e.target.closest('#profileMore')) { expanded = !expanded; render(); }
  });

  async function update(visible, gamePhase) {
    if (!visible) {
      request?.abort(); request = null; profile = null; phase = null;
      refreshAt = 0; expanded = false; render(); return;
    }
    render();
    if (phase === gamePhase && (request || Date.now() < refreshAt)) return;
    phase = gamePhase;
    request?.abort();
    const current = new AbortController();
    request = current;
    const timeout = setTimeout(() => current.abort(), 15000);
    try {
      const response = await fetch(base+'/profile', {cache:'no-store', signal:current.signal});
      const data = response.ok ? await response.json() : null;
      if (request !== current) return;
      if (data?.name !== profile?.name || data?.tag !== profile?.tag) expanded = false;
      profile = data?.name ? data : null;
    } catch {
      if (request === current) profile = null;
    } finally {
      clearTimeout(timeout);
      if (request === current) {
        request = null;
        refreshAt = Date.now() + (profile ? 30000 : 10000);
        render();
      }
    }
  }
  return {update};
}
