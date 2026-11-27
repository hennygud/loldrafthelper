"""Read the signed-in player's profile, current Solo/Duo record and recent games."""
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import quote


def optional_get(client, path, timeout=None):
    try:
        return client.get(path) if timeout is None else client.get(path, timeout=timeout)
    except (ConnectionError, HTTPError, ValueError, TypeError):
        return None


def season_record(ranked):
    if not isinstance(ranked, dict):
        return None
    queue = (ranked.get("queueMap") or {}).get("RANKED_SOLO_5x5")
    if not queue:
        queue = next((q for q in ranked.get("queues") or []
                      if q.get("queueType") == "RANKED_SOLO_5x5"), None)
    if not queue:
        return {"tier": "UNRANKED"} if "queues" in ranked or "queueMap" in ranked else None
    return {key: queue.get(key) for key in ("tier", "division", "leaguePoints", "wins", "losses")}


def own_participant(game, summoner):
    players = game.get("participants") or []

    def is_self(player):
        if player.get("puuid"):
            return player["puuid"] == summoner["puuid"]
        return any(summoner.get(k) and str(player.get(k)) == str(summoner[k])
                   for k in ("summonerId", "accountId"))

    identities = game.get("participantIdentities") or []
    ids = {entry["participantId"] for entry in identities
           if "participantId" in entry and is_self(entry.get("player") or {})}
    me = next((p for p in players if is_self(p) or p.get("participantId") in ids), None)
    # A scoped history summary may contain just the requesting player, without identifiers.
    if not me and len(players) == 1 and not identities and not any(
        players[0].get(k) for k in ("puuid", "summonerId", "accountId")
    ):
        me = players[0]
    return me


def own_match(game, summoner):
    me = own_participant(game, summoner)
    if not me:
        return None
    stats = me.get("stats") or me
    duration = game.get("gameDuration")
    result = None
    if stats.get("gameEndedInEarlySurrender") and duration and duration < 240:
        result = "remake"
    elif isinstance(stats.get("win"), bool):
        result = "win" if stats["win"] else "loss"
    return {"game": game.get("gameId"), "champion": me.get("championId"), "result": result,
            "kda": [stats.get(k) for k in ("kills", "deaths", "assists")],
            "timestamp": game.get("gameCreation"), "duration": duration}


def read_profile(client):
    summoner = optional_get(client, "/lol-summoner/v1/current-summoner")
    if not summoner or not summoner.get("puuid"):
        return None
    puuid = quote(summoner["puuid"], safe="")
    ranked = optional_get(client, "/lol-ranked/v1/current-ranked-stats")
    history = optional_get(client, f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex=9")
    games = ((history or {}).get("games") or {}).get("games")
    matches = None
    if isinstance(games, list):
        recent = sorted(games, key=lambda g: g.get("gameCreation") or 0, reverse=True)
        matches = [match for g in recent if (match := own_match(g, summoner)) is not None][:10]
    return {"name": summoner.get("gameName") or summoner.get("displayName"),
            "tag": summoner.get("tagLine"), "level": summoner.get("summonerLevel"),
            "icon": summoner.get("profileIconId"), "rank": season_record(ranked), "matches": matches}


# ─────────────── последние игры для окна анализа ───────────────
# История в клиенте отдаёт по каждой игре только вас. Полный состав (10 чемпионов, роли, баны)
# лежит в /lol-match-history/v1/games/{id}. Сыгранные игры не меняются, поэтому их можно кэшировать.

GAME_CACHE = {}
SMITE = 11
POSITION_KEYS = ("teamPosition", "individualPosition", "selectedPosition")
POSITIONS = {"TOP": "T", "JUNGLE": "J", "MIDDLE": "M", "MID": "M", "BOTTOM": "B", "BOT": "B",
             "ADC": "B", "CARRY": "B", "UTILITY": "S", "SUPPORT": "S"}


def position_hint(player):
    """Подсказка клиента о роли. Старые поля lane/role часто ошибаются, поэтому это только подсказка."""
    timeline = player.get("timeline") or {}
    for key in POSITION_KEYS:
        value = str(player.get(key) or timeline.get(key) or "").upper()
        if value in POSITIONS:
            return POSITIONS[value]
    lane = str(timeline.get("lane") or "").upper()
    role = str(timeline.get("role") or "").upper()
    if lane in ("BOTTOM", "BOT"):
        return {"DUO_SUPPORT": "S", "DUO_CARRY": "B"}.get(role, "BS")
    return POSITIONS.get(lane, "")


def summarize_game(game, summoner):
    """Сжимает игру до того, что нужно модели: чемпионы, команды, подсказки ролей, баны, результат.
    Чужие имена и идентификаторы не передаются."""
    me = own_participant(game, summoner)
    if not me:
        return None
    my_team = me.get("teamId")
    players = []
    for p in game.get("participants") or []:
        stats = p.get("stats") or p
        spells = {p.get("spell1Id"), p.get("spell2Id")}
        players.append({"champ": p.get("championId"), "ally": p.get("teamId") == my_team, "me": p is me,
                        "hint": position_hint(p), "smite": SMITE in spells,
                        "kda": [stats.get(k) for k in ("kills", "deaths", "assists")]})
    bans = {"ally": [], "enemy": []}
    for team in game.get("teams") or []:
        side = "ally" if team.get("teamId") == my_team else "enemy"
        bans[side] += [b.get("championId") for b in team.get("bans") or [] if (b.get("championId") or 0) > 0]
    stats = me.get("stats") or me
    duration = game.get("gameDuration")
    win = stats.get("win") if isinstance(stats.get("win"), bool) else None
    remake = bool(stats.get("gameEndedInEarlySurrender")) and bool(duration) and duration < 240
    return {"id": game.get("gameId"), "queue": game.get("queueId"), "map": game.get("mapId"),
            "mode": game.get("gameMode"), "created": game.get("gameCreation"), "duration": duration,
            "win": win, "remake": remake, "bans": bans, "players": players}


def _game_detail(client, summary, summoner):
    gid = summary.get("gameId")
    if gid in GAME_CACHE:
        return GAME_CACHE[gid]
    detail = optional_get(client, f"/lol-match-history/v1/games/{int(gid)}", timeout=10) if isinstance(gid, int) else None
    game = summarize_game(detail, summoner) if isinstance(detail, dict) else None
    if game and len(game["players"]) >= 10:
        if len(GAME_CACHE) > 300:
            GAME_CACHE.clear()
        GAME_CACHE[gid] = game
        return game
    # Не удалось получить полный состав: показываем игру без анализа драфта.
    return summarize_game(summary, summoner)


def read_recent_games(client, count=20, workers=4):
    summoner = optional_get(client, "/lol-summoner/v1/current-summoner")
    if not summoner or not summoner.get("puuid"):
        return None
    puuid = quote(summoner["puuid"], safe="")
    history = optional_get(client, f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex={count - 1}",
                           timeout=10)
    found = ((history or {}).get("games") or {}).get("games")
    who = {"name": summoner.get("gameName") or summoner.get("displayName"), "tag": summoner.get("tagLine")}
    if not isinstance(found, list):
        return dict(who, games=None)
    recent = sorted(found, key=lambda g: g.get("gameCreation") or 0, reverse=True)[:count]
    clients = [client.clone() if hasattr(client, "clone") else client for _ in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        games = list(pool.map(lambda pair: _game_detail(clients[pair[0] % workers], pair[1], summoner), enumerate(recent)))
    return dict(who, games=[g for g in games if g])
