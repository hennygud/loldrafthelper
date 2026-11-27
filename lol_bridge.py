#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мост «Клиент LoL → Помощник драфта».

Что делает: раз в секунду смотрит в клиент League of Legends, кто кого
пикнул и забанил, и отдаёт это странице помощника драфта.

Как запустить: дважды кликните по LoLDraftHelper.exe — откроется окно помощника.
Закрыли окно — программа выключилась. Из исходников: python lol_bridge.py
(без пакета pywebview страница откроется в окне Edge или в браузере).
Флаги: --headless (только мост, без окна), --selftest ФАЙЛ (проверка сборки).

Безопасность:
  * читает только экран выбора чемпионов (как U.GG, Porofessor и др.);
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

PORT = 27500
PAGE_NAME = "lol_draft_helper.html"
VERSION = "1.0.1"  # при выпуске через GitHub Actions подставляется из тега автоматически
REPO = "hennygud/loldrafthelper"
UPDATE_URL = os.environ.get("LOL_UPDATE_URL", f"https://api.github.com/repos/{REPO}/releases/latest")
UPDATE = {"current": VERSION, "available": False}
IS_FROZEN = bool(getattr(sys, "frozen", False))      # True, когда это собранный .exe
SELF_PATH = sys.executable if IS_FROZEN else ""      # путь к самому .exe
ALLOWED_URL_PREFIX = "https://github.com/"           # качаем обновления только отсюда
MIN_EXE_SIZE = 1_000_000                             # меньше этого точно не наш exe
ORIGINS_OK = (f"http://127.0.0.1:{PORT}", "https://hennygud.github.io")
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LoLDraftHelper")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
NO_CONSOLE = sys.stdout is None                      # собрано как оконное приложение: печатать некуда
STATS_URL = os.environ.get("LOL_STATS_URL", f"https://raw.githubusercontent.com/{REPO}/main/stats.json")
STATS_PATH = os.path.join(APP_DIR, "stats.json")     # свежая копия данных матчей, скачанная из репозитория
STATS_MAX = 8_000_000
LAST_SEEN = [0.0]                                    # когда страница в последний раз обращалась к мосту


def find_page():
    """Страница лежит рядом со скриптом или внутри собранного .exe (PyInstaller)."""
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        if base and os.path.isfile(os.path.join(base, PAGE_NAME)):
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
        req = urllib.request.Request(STATS_URL, headers={"User-Agent": "LoLDraftHelper"})
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
        # из .exe иконки берём только с Data Dragon: папки icons/ рядом нет
        return f.read().replace("const LOCAL_ICONS=true;", "const LOCAL_ICONS=false;").encode("utf-8")
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
        if not self.base and not self.connect():
            raise ConnectionError("no client")
        req = urllib.request.Request(self.base + path, headers=self.hdr)
        try:
            with urllib.request.urlopen(req, timeout=3, context=_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
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

def read_state(cl):
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
    s = cl.get("/lol-champ-select/v1/session")
    if not s:
        return st
    st["champSelect"] = True
    me_cell = s.get("localPlayerCellId")
    my_cells = {m.get("cellId") for m in s.get("myTeam", [])}

    def player(m, hover_ok):
        cid = m.get("championId") or 0
        hover = False
        if cid <= 0 and hover_ok:
            cid, hover = m.get("championPickIntent") or 0, True
        c = cl.champ(cid)
        return {"cell": m.get("cellId"), "pos": (m.get("assignedPosition") or "").lower(),
                "alias": c["alias"] if c else "", "name": c["name"] if c else "",
                "hover": bool(c) and hover, "me": m.get("cellId") == me_cell}

    st["ally"] = [player(m, True) for m in s.get("myTeam", [])]
    st["enemy"] = [player(m, False) for m in s.get("theirTeam", [])]
    me = next((p for p in st["ally"] if p["me"]), None)
    st["me"] = me["pos"] if me else ""

    seen = set()
    for group in s.get("actions", []):
        for a in group:
            if a.get("type") == "ban" and a.get("completed") and (a.get("championId") or 0) > 0:
                if a.get("id") in seen:
                    continue
                seen.add(a.get("id"))
                c = cl.champ(a["championId"])
                if c:
                    side = "ally" if a.get("actorCellId") in my_cells else "enemy"
                    st["bans"][side].append(c["alias"])
    if not st["bans"]["ally"] and not st["bans"]["enemy"]:  # запасной путь для старых клиентов
        b = s.get("bans") or {}
        for side, key in (("ally", "myTeamBans"), ("enemy", "theirTeamBans")):
            for cid in b.get(key, []) or []:
                c = cl.champ(cid)
                if c:
                    st["bans"][side].append(c["alias"])

    t = s.get("timer") or {}
    if t.get("adjustedTimeLeftInPhase") is not None:
        st["timer"] = max(0, int(t["adjustedTimeLeftInPhase"] / 1000))
        st["timerPhase"] = t.get("phase", "")
    return st


def vtuple(s):
    n = [int(x) for x in re.findall(r"\d+", str(s))][:3]
    return tuple(n + [0] * (3 - len(n)))


def check_update():
    """Спрашивает у GitHub, нет ли версии новее. Любая ошибка (нет интернета, лимит) просто игнорируется."""
    info = {"current": VERSION, "available": False}
    try:
        req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": "LoLDraftHelper", "Accept": "application/vnd.github+json"})
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
    req = urllib.request.Request(url, headers={"User-Agent": "LoLDraftHelper"})
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


def do_update():
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
    relaunch()
    exit_soon()
    return True, ""


def cleanup_old():
    """Стирает .old от прошлого обновления; старый процесс может ещё секунду-две держать файл."""
    if not SELF_PATH:
        return
    old = SELF_PATH + ".old"
    for _ in range(40):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            say("Старая версия удалена.")
            return
        except OSError:
            time.sleep(0.5)


STATE = {"bridge": 1, "client": False, "champSelect": False, "phaseName": "Запуск…"}
LOCK = threading.Lock()


def poll_loop(cl):
    last = None
    while True:
        try:
            s = read_state(cl)
        except Exception as e:  # мост не должен падать ни при каких ответах клиента
            s = dict(STATE, error=str(e))
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
<small>Окно можно не трогать. Откройте помощник драфта и нажмите «Подключить клиент».</small></div>
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
        elif self.path.startswith("/stats.json"):
            body = load_stats()
            if body is None:
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            ctype = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
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
        webview.create_window(f"Помощник драфта · {VERSION}", url, width=1380, height=920,
                              min_size=(960, 640), background_color="#0d1117")
        webview.start(private_mode=False, storage_path=store)  # private_mode=False: пул и настройки сохраняются
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
    url = f"http://127.0.0.1:{PORT}/"
    say(f"Помощник драфта {VERSION}: мост на {url} (только ваш компьютер)")
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
    threading.Thread(target=cleanup_old, daemon=True).start()
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
