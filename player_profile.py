"""Read the signed-in player's profile, current Solo/Duo record and recent games."""
from urllib.error import HTTPError
from urllib.parse import quote


def optional_get(client, path):
    try:
        return client.get(path)
    except (ConnectionError, HTTPError, ValueError):
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


def own_match(game, summoner):
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
    if not me:
        return None
    stats = me.get("stats") or me
    duration = game.get("gameDuration")
    result = None
    if stats.get("gameEndedInEarlySurrender") and duration and duration < 240:
        result = "remake"
    elif isinstance(stats.get("win"), bool):
        result = "win" if stats["win"] else "loss"
    return {"champion": me.get("championId"), "result": result,
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
