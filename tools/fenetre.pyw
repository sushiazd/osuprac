"""Le tableau de bord dans sa propre fenêtre Windows, sans navigateur ni terminal (lancé par « osu progression.exe »).

.pyw + pythonw.exe : aucune console. Le serveur Streamlit tourne caché en arrière-plan et s'arrête avec la fenêtre,
même si elle est tuée brutalement (il est rattaché à un « job » Windows qui meurt avec elle). Un deuxième lancement
ramène simplement la fenêtre déjà ouverte au premier plan. Sans console, une erreur serait invisible : le journal
du serveur est écrit dans %LOCALAPPDATA%\\osu-progression\\fenetre.log et affiché dans la fenêtre en cas d'échec.
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TITLE = "osu! progression"
ICON = ROOT / "assets" / "icon.ico"
HOME = Path(os.environ.get("LOCALAPPDATA") or ROOT) / "osu-progression"
LOG = HOME / "fenetre.log"
PORT = 8501
NO_WINDOW = subprocess.CREATE_NO_WINDOW
# Les sous-processus utilisent python.exe (et non pythonw.exe) pour que leur sortie soit capturée ; NO_WINDOW
# empêche toute console de s'ouvrir.
PYTHON = str(Path(sys.executable).with_name("python.exe"))

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

LOADING = """<!doctype html><html><head><meta charset="utf-8"><style>
:root{--paper:#f4f2ec;--sheet:#fffefb;--ink:#1c1b19;--ink-2:#55524b;--muted:#878379;--rule:#e4e0d6;--pen:#2446c8;
--bad:#c2352b}
@media (prefers-color-scheme:dark){:root{--paper:#121211;--sheet:#1b1b19;--ink:#f1eee7;--ink-2:#c3bfb5;--muted:#8f8b82;
--rule:#2e2d2a;--pen:#7592ff;--bad:#ff6b5e}}
html,body{margin:0;height:100%;background:var(--paper);color:var(--ink);font-family:Inter,"Segoe UI",sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;user-select:none}
.ring{width:56px;height:56px;border-radius:50%;border:4px solid var(--rule);border-top-color:var(--pen);
animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
h1{margin:0;font:600 30px/1.1 "Source Serif 4","Iowan Old Style","Palatino Linotype",Georgia,serif;letter-spacing:-.02em}
#status{color:var(--muted);font-size:14px}
pre{max-width:90vw;max-height:45vh;overflow:auto;background:var(--sheet);border:1px solid var(--rule);border-radius:12px;
padding:12px 14px;font-size:12px;color:var(--ink-2);white-space:pre-wrap;display:none}
.error .ring{border-color:var(--bad);animation:none}
</style></head><body>
<div class="ring"></div><h1>osu! progression</h1><div id="status">Ouverture…</div><pre id="log"></pre>
<script>
function setStatus(t){document.getElementById('status').textContent=t}
function showError(t,log){document.body.classList.add('error');setStatus(t);
var p=document.getElementById('log');p.textContent=log;p.style.display='block'}
</script></body></html>"""


def focus_existing_window() -> bool:
    """Un seul exemplaire à la fois : si la fenêtre est déjà ouverte, on la ramène devant au lieu d'en ouvrir une autre."""
    kernel32.CreateMutexW(None, False, "osu-progression-fenetre")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


class JobLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION : seul LimitFlags sert, le reste doit juste avoir la bonne taille."""
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def tie_to_window(proc: subprocess.Popen) -> None:
    """Le serveur meurt avec cette fenêtre, même si elle plante ou est fermée depuis le gestionnaire des tâches."""
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    limits = JobLimits(LimitFlags=0x2000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits))
    kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))
    tie_to_window.job = job  # garde le handle ouvert jusqu'à la fin du processus


def stop_stale_servers() -> None:
    """Ferme un ancien serveur resté ouvert sur le port du tableau de bord (ancien lancer.bat, plantage...)."""
    venv = str(ROOT / ".venv")
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'streamlit.exe','python.exe' -and "
              f"$_.CommandLine -like '*{venv}*streamlit*' -and $_.CommandLine -like '*{PORT}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=NO_WINDOW,
                   capture_output=True, timeout=30)


def free_port() -> int:
    for port in (PORT, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("aucun port libre")


def log_tail(lines: int = 40) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class Server:
    proc: subprocess.Popen | None = None
    closed = False

    @classmethod
    def stop(cls) -> None:
        cls.closed = True
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()


def run_quiet(*args: str, timeout: int) -> None:
    with open(LOG, "a", encoding="utf-8") as out:
        subprocess.run([PYTHON, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                       creationflags=NO_WINDOW, timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def boot(window) -> None:
    def status(text: str) -> None:
        window.evaluate_js(f"setStatus({json.dumps(text)})")

    try:
        LOG.write_text(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n", encoding="utf-8")
        if "--deja-a-jour" not in sys.argv:  # lancer.bat vient déjà de faire les deux
            status("Recherche de mises à jour…")
            try:
                run_quiet("tools/update.py", timeout=180)
            except subprocess.TimeoutExpired:
                pass
            # Bilan partagé avec le développeur, seulement si le joueur a accepté (question posée par lancer.bat).
            subprocess.Popen([PYTHON, "tools/diagnostic.py", "--auto"], cwd=ROOT, creationflags=NO_WINDOW,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status("Démarrage du tableau de bord…")
        stop_stale_servers()
        port = free_port()
        out = open(LOG, "a", encoding="utf-8")
        Server.proc = subprocess.Popen(
            [PYTHON, "-m", "streamlit", "run", "app.py", "--server.port", str(port), "--server.address", "127.0.0.1",
             "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        tie_to_window(Server.proc)
        if Server.closed:
            Server.stop()
            return
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not Server.closed:
            if Server.proc.poll() is not None:
                raise RuntimeError("le serveur du tableau de bord s'est arrêté")
            try:
                with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:
                    if r.status == 200:
                        window.load_url(url)
                        return
            except OSError:
                time.sleep(0.3)
        if not Server.closed:
            raise RuntimeError("le tableau de bord met trop de temps à démarrer")
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as out:
            traceback.print_exc(file=out)
        if not Server.closed:
            window.evaluate_js(f"showError({json.dumps(f'Impossible de démarrer : {e}')}, "
                               f"{json.dumps(log_tail() + chr(10) + chr(10) + f'Journal complet : {LOG}')})")


def windows_dark() -> bool:
    """Le mode sombre des applications Windows (Paramètres > Personnalisation > Couleurs)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def main() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    if focus_existing_window():
        return
    # Sa propre icône dans la barre des tâches, au lieu d'être rangé avec Python.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("osuprac.osu-progression")
    import webview

    # Fond de la fenêtre avant le premier affichage : le papier d'ESIRPRAC, clair ou sombre comme Windows.
    window = webview.create_window(TITLE, html=LOADING, width=1440, height=920, min_size=(900, 600),
                                   background_color="#121211" if windows_dark() else "#f4f2ec", text_select=True)
    window.events.closed += Server.stop
    try:
        webview.start(boot, window, gui="edgechromium", icon=str(ICON), private_mode=False,
                      storage_path=str(HOME / "webview"))
    finally:
        Server.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as out:
            traceback.print_exc(file=out)
        ctypes.windll.user32.MessageBoxW(None, f"osu! progression n'a pas pu s'ouvrir.\n\nDétails : {LOG}", TITLE, 0x10)
