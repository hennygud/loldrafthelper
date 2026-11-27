#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мост «Клиент LoL → MLK.gg».

Что делает: раз в секунду смотрит в клиент League of Legends, кто кого
пикнул и забанил, и отдаёт это странице помощника драфта.

Как запустить: откройте ярлык MLK.gg или файл MLK.gg.exe — откроется окно помощника.
Закрыли окно — программа выключилась. Из исходников: python lol_bridge.py
(без пакета pywebview страница откроется в окне Edge или в браузере).
Флаги: --headless (только мост, без окна), --selftest ФАЙЛ (проверка сборки).

Безопасность:
  * после подтверждения своего пика применяет рекомендацию клиента по рунам и заклинаниям;
  * ничего не нажимает в клиенте и не лезет в память игры;
  * слушает только ваш компьютер (127.0.0.1), снаружи к нему не подключиться.

Окно рисует pywebview (встроенный в Windows движок Edge WebView2), остальное — стандартная библиотека.
"""
import os as _os
import sys as _sys


def _restart_fresh():
    """После обновления из версии 1.0.0 новый .exe мог унаследовать временную папку старой версии
    (так устроены однофайловые сборки PyInstaller). Перезапускаемся начисто, пока старая папка жива."""
    if not getattr(_sys, "frozen", False) or "--after-update" not in _sys.argv or _os.environ.get("LOLDH_FRESH") == "1":
        return
    import subprocess as _sp
    env = {k: v for k, v in _os.environ.items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    env.update(LOLDH_FRESH="1", PYINSTALLER_RESET_ENVIRONMENT="1")
    _sp.Popen([_sys.executable] + _sys.argv[1:], env=env, close_fds=True)
    _os._exit(0)


_restart_fresh()
import base64
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from player_profile import read_profile

PORT = 27500
PAGE_NAME = "index.html"
VERSION = "1.0.1"  # при выпуске через GitHub Actions подставляется из тега автоматически
REPO = "hennygud/loldrafthelper"
UPDATE_URL = os.environ.get("LOL_UPDATE_URL", f"https://api.github.com/repos/{REPO}/releases/latest")
UPDATE = {"current": VERSION, "available": False}
IS_FROZEN = bool(getattr(sys, "frozen", False))      # True, когда это собранный .exe
SELF_PATH = sys.executable if IS_FROZEN else ""      # путь к самому .exe
ALLOWED_URL_PREFIX = "https://github.com/"           # качаем обновления только отсюда
MIN_EXE_SIZE = 1_000_000                             # меньше этого точно не наш exe
ORIGINS_OK = (f"http://127.0.0.1:{PORT}", "https://hennygud.github.io")
_DATA_DIR = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
_OLD_DIR = os.path.join(_DATA_DIR, "LoLDraftHelper")
APP_DIR = _OLD_DIR if os.path.isdir(_OLD_DIR) else os.path.join(_DATA_DIR, "mlkdrafthelper")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
NO_CONSOLE = sys.stdout is None                      # собрано как оконное приложение: печатать некуда
STATS_URL = os.environ.get("LOL_STATS_URL", f"https://raw.githubusercontent.com/{REPO}/main/stats.json")
STATS_PATH = os.path.join(APP_DIR, "stats.json")     # свежая копия данных матчей, скачанная из репозитория
STATS_MAX = 8_000_000
LAST_SEEN = [0.0]                                    # когда страница в последний раз обращалась к мосту


def find_page():
    """Страница лежит рядом со скриптом или внутри собранного .exe (PyInstaller)."""
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        if (base and os.path.isfile(os.path.join(base, PAGE_NAME))
                and os.path.isfile(os.path.join(base, "assets", "js", "draft.js"))):
            return os.path.join(base, PAGE_NAME)
    return None


def stats_ok(raw):
    """Проверка файла данных: чужой или битый файл не должен попасть в приложение."""
    try:
        d = json.loads(raw.decode("utf-8"))
        return (isinstance(d, dict) and isinstance(d.get("games"), int) and d["games"] > 0 and isinstance(d.get("patch"), str)
                and all(isinstance(d.get(k, []), list) for k in ("counters", "vs", "synergy")) and isinstance(d.get("champs", {}), dict))
    except Exception:
        return False


def load_stats():
    """Данные матчей: сначала свежая копия из репозитория, иначе та, что упакована в .exe, иначе рядом со скриптом.
    Нет файла — приложение работает по моим оценкам."""
    cands = [STATS_PATH, os.path.join(os.path.dirname(SELF_PATH), "stats.json") if SELF_PATH else "",
             os.path.join(getattr(sys, "_MEIPASS", "") or "", "stats.json"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json"), os.path.join(os.getcwd(), "stats.json")]
    for p in cands:
        if p and os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if stats_ok(raw):
                return raw
    return None


def stats_stamp():
    raw = load_stats()
    if not raw:
        return ""
    d = json.loads(raw.decode("utf-8"))
    return f"{d.get('patch')}:{d.get('games')}"


def fetch_stats():
    """Подтягивает свежие данные матчей из репозитория (без нового релиза). Ошибки сети молча игнорируются."""
    try:
        req = urllib.request.Request(STATS_URL, headers={"User-Agent": "mlkdrafthelper"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(STATS_MAX + 1)
        if len(raw) > STATS_MAX or not stats_ok(raw):
            return
        old = load_stats()
        if old == raw:
            return
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = STATS_PATH + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, STATS_PATH)
        say(f"Обновлены данные матчей: {stats_stamp()}")
    except Exception:
        pass


def load_page():
    p = find_page()
    if not p:
        return None
    with open(p, encoding="utf-8") as f:
        return f.read().encode("utf-8")
POLL_SECONDS = 1.0

LOCKFILES = [
    r"C:\Riot Games\League of Legends\lockfile",
    r"D:\Riot Games\League of Legends\lockfile",
    r"E:\Riot Games\League of Legends\lockfile",
    r"C:\Program Files\Riot Games\League of Legends\lockfile",
    "/Applications/League of Legends.app/Contents/LoL/lockfile",
]

PHASES = {
    "None": "Главное меню", "Lobby": "Лобби", "Matchmaking": "Поиск игры",
    "ReadyCheck": "Игра найдена", "ChampSelect": "Выбор чемпионов",
    "GameStart": "Загрузка", "InProgress": "Идёт игра", "WaitingForStats": "Конец игры",
    "PreEndOfGame": "Конец игры", "EndOfGame": "Конец игры", "Reconnect": "Переподключение",
}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # у клиента самоподписанный сертификат на 127.0.0.1


def say(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    if not NO_CONSOLE:
        print(line, flush=True)
    if IS_FROZEN:  # у .exe нет консоли, поэтому пишем журнал в файл
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512_000:
                os.replace(LOG_PATH, LOG_PATH + ".1")
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ─────────────── поиск клиента ───────────────

def creds_from_process():
    """Порт и пароль из командной строки LeagueClientUx (работает при любой папке установки)."""
    try:
        if platform.system() == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\" | Select-Object -ExpandProperty CommandLine"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=8,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        else:
            out = subprocess.run(["ps", "-A", "-o", "args="], capture_output=True, text=True, timeout=5).stdout
        port = re.search(r"--app-port=(\d+)", out)
        token = re.search(r"--remoting-auth-token=([\w-]+)", out)
        if port and token:
            return "https", port.group(1), token.group(1)
    except Exception:
        pass
    return None


def creds_from_lockfile(extra=None):
    paths = ([extra] if extra else []) + [os.environ.get("LOL_LOCKFILE", "")] + LOCKFILES
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    _, _, port, pw, proto = f.read().strip().split(":")[:5]
                return proto, port, pw
            except Exception:
                continue
    return None


class Client:
    def __init__(self, lockfile=None):
        self.lockfile = lockfile
        self.base = None
        self.hdr = None
        self.champs = {}

    def connect(self):
        c = creds_from_lockfile(self.lockfile) or creds_from_process()
        if not c:
            return False
        proto, port, pw = c
        self.base = f"{proto}://127.0.0.1:{port}"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(f"riot:{pw}".encode()).decode(),
                    "Accept": "application/json"}
        self.champs = {}
        return True

    def get(self, path):
        return self.request("GET", path)

    def request(self, method, path, data=None, *, binary=False):
        if not self.base and not self.connect():
            raise ConnectionError("no client")
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={**self.hdr, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3, context=_CTX) as r:
                body = r.read()
                if binary:
                    return body
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None  # например, мы сейчас не в выборе чемпионов
            raise
        except (urllib.error.URLError, OSError):
            self.base = None  # клиент закрыли или перезапустили
            raise ConnectionError("client gone")

    def champ(self, cid):
        if not cid or cid <= 0:
            return None
        if not self.champs:
            data = self.get("/lol-game-data/assets/v1/champion-summary.json") or []
            # alias — английский ключ (MonkeyKing, Nunu…), name — имя на языке клиента
            self.champs = {c["id"]: {"alias": c.get("alias", ""), "name": c.get("name", "")}
                           for c in data if c.get("id", -1) > 0}
        return self.champs.get(cid)


# ─────────────── чтение выбора чемпионов ───────────────

SESSION = "/lol-champ-select/v1/session"


def player_pick(session, member, ally):
    """Во время выбора championId может быть уже заполнен. После обмена в нём актуальный пик."""
    cell = member["cellId"]
    picks = [a for group in session.get("actions", []) for a in group
             if a.get("type") == "pick" and a.get("actorCellId") == cell]
    roster = member.get("championId") or 0
    completed = next((a for a in reversed(picks) if a.get("completed") and a.get("championId", 0) > 0), None)
    if completed:
        return roster or completed["championId"], "locked"
    if roster > 0 and (not picks or (session.get("timer") or {}).get("phase") == "FINALIZATION"):
        return roster, "locked"
    pending = next((a["championId"] for a in picks if a.get("isInProgress") and a.get("championId", 0) > 0), 0)
    cid = pending or roster or (member.get("championPickIntent") if ally else 0)
    return (cid, "hover") if cid and cid > 0 else (0, "empty")


def loadout_target(session):
    if session and not session.get("isSpectating"):
        for member in session.get("myTeam", []):
            if member["cellId"] == session.get("localPlayerCellId"):
                cid, status = player_pick(session, member, True)
                if status == "locked":
                    return (session.get("gameId") or session.get("id"), cid,
                            (member.get("assignedPosition") or "").upper())
    return None


def read_state(cl, loadout=None):
    st = {"bridge": 1, "client": False, "phase": None, "phaseName": "", "champSelect": False,
          "me": "", "ally": [], "enemy": [], "bans": {"ally": [], "enemy": []},
          "timer": None, "timerPhase": "", "updated": time.time()}
    try:
        phase = cl.get("/lol-gameflow/v1/gameflow-phase")
    except ConnectionError:
        return st
    st["client"] = True
    st["phase"] = phase
    st["phaseName"] = PHASES.get(phase, str(phase))
    if phase != "ChampSelect":
        return st
    s = cl.get(SESSION)
    if not s:
        return st
    st["champSelect"] = True
    me_cell = s.get("localPlayerCellId")
    my_cells = {m.get("cellId") for m in s.get("myTeam", [])}

    def player(m, ally):
        cid, status = player_pick(s, m, ally)
        c = cl.champ(cid)
        return {"cell": m.get("cellId"), "pos": (m.get("assignedPosition") or "").lower(),
                "alias": c["alias"] if c else "", "name": c["name"] if c else "",
                "championId": cid, "status": status, "hover": status == "hover",
                "me": ally and m.get("cellId") == me_cell}

    st["ally"] = [player(m, True) for m in s.get("myTeam", [])]
    st["enemy"] = [player(m, False) for m in s.get("theirTeam", [])]
    me = next((p for p in st["ally"] if p["me"]), None)
    st["me"] = me["pos"] if me else ""

    ban_ids = {"ally": [], "enemy": []}
    for group in s.get("actions", []):
        for action in group:
            if action.get("type") == "ban" and action.get("completed"):
                side = "ally" if action.get("actorCellId") in my_cells else "enemy"
                ban_ids[side].append(action.get("championId"))
    for side, key in (("ally", "myTeamBans"), ("enemy", "theirTeamBans")):
        ban_ids[side].extend((s.get("bans") or {}).get(key) or [])
        for cid in dict.fromkeys(ban_ids[side]):
            if cid and cid > 0:
                c = cl.champ(cid) or {}
                st["bans"][side].append(c.get("alias") or c.get("name") or f"#{cid}")

    t = s.get("timer") or {}
    if t.get("adjustedTimeLeftInPhase") is not None:
        st["timer"] = max(0, int(t["adjustedTimeLeftInPhase"] / 1000))
        st["timerPhase"] = t.get("phase", "")
    if loadout is not None:
        st["loadout"] = loadout.sync(cl, s)
    return st


POSITIONS = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}


def recommended_page(cl, cid, position, map_id):
    if map_id:
        pages = cl.get(f"/lol-perks/v1/recommended-pages/champion/{cid}/position/{position}/map/{map_id}")
        if pages:
            return pages[0]
    raise ValueError("Для этого чемпиона и карты пока нет рекомендации клиента.")


def ordered_spells(rec, current):
    ids = rec.get("summonerSpellIds") or []
    if len(ids) != 2 or len(set(ids)) != 2 or not all(ids):
        raise ValueError("Клиент не предложил два заклинания.")
    if ids[1] == current.get("spell1Id") or ids[0] == current.get("spell2Id"):
        return ids[::-1]
    return ids


def preview_loadout(cl, cid, position):
    """Preview Riot's page for a lobby map, or Summoner's Rift before joining a lobby."""
    try:
        flow = cl.get("/lol-gameflow/v1/session") or {}
        map_id = (flow.get("map") or {}).get("id")
        if not map_id:
            lobby = cl.get("/lol-lobby/v2/lobby") or {}
            map_id = (lobby.get("gameConfig") or {}).get("mapId") or 11
        rec = recommended_page(cl, cid, position, map_id)
        perks = rec.get("perks") or []
        keystone = perks[0]["id"] if perks else None
        if not keystone:
            return {"status": "unavailable"}
        current = cl.get(SESSION + "/my-selection") or {}
        return {"status": "ready", "keystone": keystone, "spells": ordered_spells(rec, current), "map": map_id}
    except ConnectionError:
        return {"status": "offline"}
    except (urllib.error.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "unavailable"}


class Loadout:
    page_name = "MLK.gg"

    def __init__(self):
        self.reset()

    def reset(self):
        self.target = None
        self.done = set()
        self.message = ""
        self.retry_at = 0

    def check_pick(self, cl):
        if loadout_target(cl.get(SESSION)) != self.target:
            raise ValueError("Пик изменился.")

    def recommendation(self, cl):
        _, cid, position = self.target
        flow = cl.get("/lol-gameflow/v1/session") or {}
        map_id = (flow.get("map") or {}).get("id")
        if position not in POSITIONS:
            position = cl.get(f"/lol-perks/v1/recommended-pages-position/champion/{cid}") or "NONE"
        return recommended_page(cl, cid, position, map_id)

    def runes(self, cl, rec):
        path = "/lol-perks/v1/pages"
        perks = [p["id"] for p in rec.get("perks", [])]
        if len(perks) != 9 or not all(perks) or not all(rec.get(k) for k in ("primaryPerkStyleId", "secondaryPerkStyleId")):
            raise ValueError("Клиент прислал неполную страницу рун.")
        page = {"name": self.page_name, "primaryStyleId": rec["primaryPerkStyleId"],
                "subStyleId": rec["secondaryPerkStyleId"], "selectedPerkIds": perks}
        pages = cl.get(path)
        if pages is None:
            raise ValueError("Страницы рун недоступны.")
        own = next((p for p in pages if p.get("name") in (self.page_name, "MLK Helper", "LoL Draft Helper") and p.get("isEditable")), None)
        if own:
            page_id = own["id"]
            if any(own.get(k) != v for k, v in page.items()):
                self.check_pick(cl)
                cl.request("PUT", f"{path}/{page_id}", dict(page, id=page_id))
        else:
            inventory = cl.get("/lol-perks/v1/inventory") or {}
            if inventory.get("canAddCustomPage") is False:
                raise ValueError("Для страницы MLK.gg нужен свободный слот.")
            self.check_pick(cl)
            page_id = cl.request("POST", path, page)["id"]
        self.check_pick(cl)
        cl.request("PUT", "/lol-perks/v1/currentpage", page_id)

    def spells(self, cl, rec):
        path = SESSION + "/my-selection"
        current = cl.get(path) or {}
        ids = ordered_spells(rec, current)
        if ids == [current.get("spell1Id"), current.get("spell2Id")]:
            return
        self.check_pick(cl)
        cl.request("PATCH", path, {"spell1Id": ids[0], "spell2Id": ids[1]})

    def sync(self, cl, session):
        target = loadout_target(session)
        if target != self.target:
            self.reset()
            self.target = target
        if not target or len(self.done) == 2 or time.monotonic() < self.retry_at:
            return self.message
        self.retry_at = time.monotonic() + 10
        try:
            rec = self.recommendation(cl)
        except (ConnectionError, urllib.error.HTTPError, ValueError) as err:
            self.message = f"Подбор рун и заклинаний: {err}"
            return self.message
        messages = []
        for label, apply in (("Руны", self.runes), ("Заклинания", self.spells)):
            try:
                if label not in self.done:
                    apply(cl, rec)
                    self.done.add(label)
                messages.append(label + " применены.")
            except (ConnectionError, urllib.error.HTTPError, ValueError, KeyError, TypeError) as err:
                messages.append(f"{label}: {err}")
        self.message = " ".join(messages)
        return self.message


def vtuple(s):
    n = [int(x) for x in re.findall(r"\d+", str(s))][:3]
    return tuple(n + [0] * (3 - len(n)))


def check_update():
    """Спрашивает у GitHub, нет ли версии новее. Любая ошибка (нет интернета, лимит) просто игнорируется."""
    info = {"current": VERSION, "available": False}
    try:
        req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": "mlkdrafthelper", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        tag = str(d.get("tag_name", ""))
        if tag and vtuple(tag) > vtuple(VERSION):
            exe = next((a.get("browser_download_url") for a in d.get("assets", [])
                        if str(a.get("name", "")).lower().endswith(".exe")), None)
            sha = next((a.get("browser_download_url") for a in d.get("assets", [])
                        if str(a.get("name", "")).lower().endswith(".sha256")), None)
            info.update({"available": True, "latest": tag.lstrip("vV"), "url": exe or d.get("html_url", ""), "exe": bool(exe), "sha": sha,
                         "page": d.get("html_url", ""), "notes": (d.get("body") or "")[:400]})
    except Exception:
        pass
    with LOCK:
        was = UPDATE.get("latest") if UPDATE.get("available") else None
        UPDATE.clear()
        UPDATE.update(info)
    if info["available"] and info["latest"] != was:
        say(f"Доступна новая версия {info['latest']} (у вас {VERSION}): {info['page']}")


def update_loop():
    while True:
        fetch_stats()
        check_update()
        time.sleep(6 * 3600)


# ─────────────── обновление на месте: скачать → проверить → заменить себя → удалить старую версию ───────────────

def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "mlkdrafthelper"})
    with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def relaunch():
    # новая версия должна распаковаться заново, а не брать временную папку этой
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    env.update(LOLDH_FRESH="1", PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([SELF_PATH, "--after-update"], env=env, close_fds=True)


def exit_soon():
    threading.Timer(1.2, lambda: os._exit(0)).start()


UPDATE_FILE_LOCK = threading.Lock()


def do_update():
    if not UPDATE_FILE_LOCK.acquire(blocking=False):
        return False, "Обновление уже выполняется."
    restarting = False
    try:
        restarting, message = _install_update()
        return restarting, message
    finally:
        # После запуска новой версии только она должна убирать резервную копию.
        if not restarting:
            UPDATE_FILE_LOCK.release()


def _install_update():
    """Возвращает (успех, текст ошибки). Старый файл переименовывается в .old и стирается новой версией."""
    import hashlib
    if not SELF_PATH:
        return False, "Автообновление работает только в .exe. Скачайте новую версию по кнопке."
    with LOCK:
        info = dict(UPDATE)
    url = str(info.get("url", ""))
    if not (info.get("available") and info.get("exe") and url.startswith(ALLOWED_URL_PREFIX) and url.lower().endswith(".exe")):
        return False, "В релизе нет файла .exe для обновления."
    new, old = SELF_PATH + ".new", SELF_PATH + ".old"
    try:
        say(f"Скачиваю версию {info.get('latest')}…")
        _download(url, new)
        if os.path.getsize(new) < MIN_EXE_SIZE:
            raise ValueError("файл слишком маленький")
        with open(new, "rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("это не программа Windows")
        sha_url = info.get("sha")
        if sha_url:
            _download(sha_url, new + ".sha")
            with open(new + ".sha", encoding="utf-8") as f:
                want = f.read().split()[0].strip().lower()
            h = hashlib.sha256()
            with open(new, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != want:
                raise ValueError("контрольная сумма не совпала, файл повреждён")
    except Exception as e:
        for x in (new, new + ".sha"):
            try:
                os.remove(x)
            except OSError:
                pass
        say(f"Обновление не удалось: {e}")
        return False, f"Не удалось скачать обновление: {e}"
    try:
        os.remove(new + ".sha")
    except OSError:
        pass
    try:
        if os.path.exists(old):
            os.remove(old)
        os.replace(SELF_PATH, old)      # работающий .exe на Windows можно переименовать, но нельзя удалить
        try:
            os.replace(new, SELF_PATH)
            relaunch()
        except Exception:
            os.replace(old, SELF_PATH)  # не вышло: возвращаем всё как было
            raise
    except Exception as e:
        try:
            os.remove(new)
        except OSError:
            pass
        say(f"Не получилось заменить файл: {e}")
        return False, f"Не получилось заменить файл: {e}"
    say("Новая версия на месте, перезапускаюсь…")
    exit_soon()
    return True, ""


def cleanup_old():
    """Удаляет резервную копию, когда Windows освободит её после перезапуска."""
    if not SELF_PATH:
        return
    old = SELF_PATH + ".old"
    delay = 0.5
    reported = False
    while True:
        try:
            with UPDATE_FILE_LOCK:
                if not os.path.isfile(SELF_PATH):
                    return
                os.remove(old)
            say("Старая версия удалена.")
            return
        except FileNotFoundError:
            return
        except OSError as err:
            if not reported:
                say(f"Старая версия пока недоступна для удаления, повторю попытку: {err}")
                reported = True
            time.sleep(delay)
            delay = min(delay * 2, 15)


STATE = {"bridge": 1, "client": False, "champSelect": False, "phaseName": "Запуск…"}
LOCK = threading.Lock()


def poll_loop(cl):
    last = None
    loadout = Loadout()
    while True:
        try:
            s = read_state(cl, loadout)
            if not s["champSelect"]:
                loadout.reset()
        except Exception as e:  # мост не должен падать ни при каких ответах клиента
            s = {"bridge": 1, "client": False, "champSelect": False, "error": str(e)}
        with LOCK:
            STATE.clear()
            STATE.update(s)
        label = ("клиент не найден" if not s.get("client") else s.get("phaseName", ""))
        if label != last:
            last = label
            if not s.get("client"):
                say("Жду клиент League of Legends… (запустите его, я подхвачу сам)")
            else:
                say(f"Клиент на связи · {label}")
        time.sleep(POLL_SECONDS)


# ─────────────── маленький сервер для страницы ───────────────

STATUS_PAGE = """<!DOCTYPE html><html lang="ru"><meta charset="utf-8"><title>Мост LoL</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#010a13;color:#f0e6d2;font:16px/1.5 system-ui,sans-serif}
.c{border:2px solid #c8aa6e;padding:28px 34px;max-width:440px;background:#0b1a2e}h1{font:400 28px Georgia,serif;color:#c8aa6e;margin:0 0 10px}
.s{font-size:20px;margin:6px 0}.ok{color:#0ac8b9}.wait{color:#f0b35a}small{color:#a09b8c}</style>
<div class="c"><h1>Мост работает</h1><p class="s" id="s">…</p>
<small>Окно можно не трогать. Откройте помощник драфта и зайдите в выбор чемпионов.</small></div>
<script>async function u(){try{const d=await(await fetch('/state')).json();const e=document.getElementById('s');
e.className='s '+(d.client?'ok':'wait');e.textContent=d.client?('Клиент на связи · '+d.phaseName):'Жду клиент League of Legends…'}catch(_){}}
u();setInterval(u,1500)</script>"""


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/state") or self.path.startswith("/ping"):
            LAST_SEEN[0] = time.time()
        if self.path.startswith("/ping"):
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        if self.path.startswith("/state"):
            with LOCK:
                data = dict(STATE, version=VERSION, stats=stats_stamp(), update=dict(UPDATE, canUpdate=bool(SELF_PATH)))
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        elif self.path == "/profile":
            if self.headers.get("Origin") and self.headers["Origin"] not in ORIGINS_OK:
                self.send_error(403)
                return
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                self.send_error(403)
                return
            client = Client(getattr(self.server, "lockfile", None))
            body = json.dumps(read_profile(client), ensure_ascii=False).encode()
            ctype = "application/json; charset=utf-8"
        elif (icon := re.fullmatch(r"/profile-icon/([0-9]{1,6})\.jpg", self.path)):
            client = Client(getattr(self.server, "lockfile", None))
            try:
                body = client.request("GET", f"/lol-game-data/assets/v1/profile-icons/{icon[1]}.jpg", binary=True)
            except (ConnectionError, urllib.error.HTTPError):
                body = None
            if not body:
                self.send_error(404)
                return
            ctype = "image/jpeg"
        elif (preview := re.fullmatch(r"/preview/([1-9][0-9]{0,5})/(TOP|JUNGLE|MIDDLE|BOTTOM|UTILITY)", self.path)):
            client = Client(getattr(self.server, "lockfile", None))
            body = json.dumps(preview_loadout(client, int(preview[1]), preview[2])).encode()
            ctype = "application/json; charset=utf-8"
        elif re.fullmatch(r"/assets/(?:css/[\w-]+\.css|js/[\w-]+\.js|img/[\w-]+\.(?:png|jpg|ico))", self.path, re.ASCII):
            page = find_page()
            path = os.path.join(os.path.dirname(page), self.path[1:]) if page else ""
            if not os.path.isfile(path):
                self.send_error(404)
                return
            with open(path, "rb") as f:
                body = f.read()
            ctype = {".css": "text/css", ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
                     ".ico": "image/x-icon"}[os.path.splitext(path)[1]]
        elif self.path.startswith("/stats.json"):
            body = load_stats()
            if body is None:
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            ctype = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html", "/lol_draft_helper.html"):
            body, ctype = (load_page() or STATUS_PAGE.encode("utf-8")), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if self.path != "/update":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        # обновлять может только сама страница помощника, а не любой сайт в интернете
        if (origin and origin not in ORIGINS_OK) or self.headers.get("X-LoL-Update") != "1":
            self.send_response(403)
            self._cors()
            self.end_headers()
            return
        ok, err = do_update()
        body = json.dumps({"ok": ok, "error": err}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # не засоряем окно


# ─────────────── окно приложения ───────────────

class WindowControls:
    def toggle_fullscreen(self):
        self._window.toggle_fullscreen()

    def snap_at_top(self):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            point, info = wintypes.POINT(), MonitorInfo()
            info.size = ctypes.sizeof(info)
            if not user32.GetCursorPos(ctypes.byref(point)):
                return False
            monitor = user32.MonitorFromPoint(point, 2)
            at_top = user32.GetMonitorInfoW(monitor, ctypes.byref(info)) and point.y <= info.monitor.top + 8
        elif sys.platform == "darwin":
            from AppKit import NSEvent, NSScreen
            point = NSEvent.mouseLocation()
            at_top = any(frame.origin.x <= point.x < frame.origin.x + frame.size.width
                         and 0 <= frame.origin.y + frame.size.height - point.y <= 8
                         for frame in (screen.frame() for screen in NSScreen.screens()))
        else:
            return False
        if at_top:
            self.toggle_fullscreen()
        return bool(at_top)

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()


def open_window(url):
    """Своё окно (Edge WebView2 через pywebview). Возвращает True, когда окно закрыли; False — если открыть не вышло."""
    try:
        import webview
    except Exception as e:
        say(f"Своё окно недоступно ({e})")
        return False
    try:
        store = os.path.join(APP_DIR, "webview")
        os.makedirs(store, exist_ok=True)
        controls = WindowControls()
        controls._window = webview.create_window(
            "MLK.gg", url, width=1380, height=920, min_size=(960, 640),
            frameless=True, easy_drag=False, js_api=controls, background_color="#0b0d14")
        page = find_page()
        icon_name = "logo.ico" if sys.platform == "win32" else "logo.png"
        icon = os.path.join(os.path.dirname(page), "assets", "img", icon_name) if page else None
        webview.start(private_mode=False, storage_path=store, icon=icon)  # private_mode=False: пул и настройки сохраняются
        return True
    except Exception as e:
        say(f"Не удалось открыть окно: {e}")
        return False


def open_app_mode(url):
    """Запасной вариант: окно Edge/Chrome без адресной строки."""
    if platform.system() != "Windows":
        return False
    roots = [os.environ.get(v, "") for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    exes = [os.path.join(r, *p) for r in roots if r for p in
            (("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe"))]
    for exe in exes:
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1380,920"], close_fds=True)
                return True
            except OSError:
                pass
    return False


def selftest(out):
    """Проверка свежей сборки в GitHub Actions: всё ли упаковалось. Пишет ok или ошибку в файл."""
    lines = []
    try:
        assert find_page(), "страница не упакована"
        import webview  # noqa: F401
        from webview.platforms import winforms  # подгружает .NET и библиотеки WebView2
        lines = ["ok", f"version {VERSION}", f"renderer {getattr(winforms, 'renderer', '?')}"]
        code = 0
    except BaseException as e:
        lines, code = [f"fail {type(e).__name__}: {e}"], 1
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os._exit(code)


def main():
    lockfile = None
    if "--lockfile" in sys.argv:
        lockfile = sys.argv[sys.argv.index("--lockfile") + 1]
    if "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
    headless = "--headless" in sys.argv
    threading.Thread(target=cleanup_old, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"
    say(f"MLK.gg {VERSION}: мост на {url} (только ваш компьютер)")
    srv = None
    for _ in range(40 if "--after-update" in sys.argv else 1):  # после обновления старый мост ещё закрывается
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.5)
    if srv is None:
        say(f"Порт {PORT} занят: помощник уже запущен, открываю ещё одно окно к нему.")
        if not headless and not open_window(url):
            open_app_mode(url) or webbrowser.open(url)
        return
    srv.lockfile = lockfile
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=poll_loop, args=(Client(lockfile),), daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not headless:
        if open_window(url):  # окно закрыли — выключаемся целиком
            say("Окно закрыто, выход.")
            os._exit(0)
        if not open_app_mode(url):
            webbrowser.open(url)
        say("Открыто в браузере. Если нет: " + url)
    started = time.time()
    try:
        while True:
            time.sleep(5)
            # у .exe нет консоли, чтобы его закрыть: выключаемся сами, когда страницу закрыли
            if NO_CONSOLE and not headless:
                idle = time.time() - (LAST_SEEN[0] or started)
                if idle > (180 if LAST_SEEN[0] else 300):
                    say("Страница давно закрыта, выход.")
                    os._exit(0)
    except KeyboardInterrupt:
        say("Мост остановлен.")


if __name__ == "__main__":
    main()
