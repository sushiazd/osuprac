"""Calcul des pp avec le code officiel d'osu! : les mêmes valeurs que le classement « Live » de
pp.huismetbenen.nl, détail Aim / Tap / Acc / Reading / FL compris.

Le calcul tourne dans tools/ppcalc (petit programme .NET construit sur ppy/osu, voir tools/ppcalc/README.md),
lancé une fois puis interrogé ligne par ligne. Sans .NET, on retombe sur rosu-pp, plus approximatif
(pas de Reading, écart de quelques pourcents) : `engine` dit lequel a servi.
"""
import json
import os
import subprocess
import threading
from importlib.metadata import version
from pathlib import Path

import rosu_pp_py as rosu

from .osufile import get_osu_bytes, osu_path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "tools" / "ppcalc"
ENGINE_DLL = ENGINE_DIR / "ppcalc.dll"
LIVE, ROSU = "osu! (officiel)", "rosu-pp (approché)"
SKILLS = ("aim", "speed", "accuracy", "reading", "flashlight")
SKILL_LABELS = {"aim": "Aim", "speed": "Tap", "accuracy": "Acc", "reading": "Reading", "flashlight": "FL"}
FC_ACCURACIES = (0.80, 0.85, 0.90, 0.95, 1.0)  # précisions des pp d'un full combo gardés avec chaque map (pp_fc)
BONUS_MAX = 416.6667  # pp offerts selon le nombre de maps classées jouées (formule d'osu!)


def dotnet_exe() -> str | None:
    """dotnet.exe disposant du runtime .NET 8, où qu'il soit installé (global ou dans le dossier utilisateur)."""
    candidates = [os.getenv("DOTNET_ROOT"), str(Path.home() / ".dotnet"),
                  os.path.join(os.getenv("ProgramFiles", r"C:\Program Files"), "dotnet")]
    for root in filter(None, candidates):
        runtimes = Path(root) / "shared" / "Microsoft.NETCore.App"
        if (Path(root) / "dotnet.exe").exists() and runtimes.is_dir() and any(runtimes.glob("8.*")):
            return str(Path(root) / "dotnet.exe")
    return None


class _Engine:
    """Le programme .NET, gardé ouvert : le démarrer coûte ~1 s, un calcul quelques millisecondes."""

    def __init__(self, dotnet: str):
        self.proc = subprocess.Popen(
            [dotnet, str(ENGINE_DLL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.lock = threading.Lock()

    def ask(self, request: dict) -> dict:
        with self.lock:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("le calculateur de pp s'est arrêté")
        return json.loads(line)

    def alive(self) -> bool:
        return self.proc.poll() is None


_engine: _Engine | None = None
_engine_lock = threading.Lock()
_engine_failed = False


def _get_engine() -> _Engine | None:
    global _engine, _engine_failed
    with _engine_lock:
        if _engine is not None and _engine.alive():
            return _engine
        if _engine_failed or not ENGINE_DLL.exists():
            return None
        dotnet = dotnet_exe()
        if dotnet is None:
            _engine_failed = True
            return None
        try:
            _engine = _Engine(dotnet)
        except OSError:
            _engine_failed = True
            return None
        return _engine


def engine_name() -> str:
    return LIVE if _get_engine() is not None else ROSU


def engine_version() -> str:
    """Version exacte du calcul : quand elle change, les pp déjà calculés sont refaits."""
    engine = _get_engine()
    if engine is not None:
        try:
            return "live:" + engine.ask({"cmd": "version"})["commit"]
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
    return "rosu:" + version("rosu-pp-py")


def calculate(beatmap_id: int, mods: list[dict] | None = None, scores: list[dict] = ()) -> dict:
    """Difficulté de la map et pp de chaque partie demandée.

    Une partie : {"acc": 0.95, "miss": 0} (statistiques déduites comme sur huismetbenen) ou les statistiques
    réelles {"n300", "n100", "n50", "miss", "combo", "slider_tail_hit", "large_tick_miss"}.
    Les mods sont pris tels quels (NF baisse les pp d'une partie) : pour une map, passer difficulty_mods().
    Renvoie {"engine", "difficulty": {...}, "scores": [{"pp", "aim", "speed", "accuracy", "reading", ...}]}.
    """
    get_osu_bytes(beatmap_id)  # télécharge la map si besoin
    return calculate_file(osu_path(beatmap_id), mods, scores)


def calculate_file(path: Path, mods: list[dict] | None = None, scores: list[dict] = ()) -> dict:
    mods = [{"acronym": m["acronym"], **({"settings": m["settings"]} if m.get("settings") else {})}
            for m in mods or []]
    engine = _get_engine()
    if engine is not None:
        try:
            out = engine.ask({"path": str(path), "mods": mods, "scores": list(scores)})
        except (OSError, RuntimeError, ValueError):
            out = None
        if out and out.get("ok"):
            diff = out["difficulty"]
            if mods:  # AR/OD tels que ressentis : osu! les garde sans l'effet de la vitesse (DT, HT)
                base = rosu.BeatmapAttributesBuilder(map=rosu.Beatmap(path=str(path)), mods=mods).build()
                diff.update(cs=base.cs, ar=base.ar, od=base.od, hp=base.hp)
            return {"engine": LIVE, "difficulty": diff, "scores": out["scores"]}
        if out and "error" in out:
            raise ValueError(out["error"])
    return _rosu(Path(path).read_bytes(), mods, scores)


def official(path: Path, mods: list[dict]) -> tuple[float, list[float]] | None:
    """Étoiles affichées par osu! et pp d'un full combo à chaque précision de FC_ACCURACIES
    (None sans le calculateur officiel)."""
    if _get_engine() is None:
        return None
    try:
        res = calculate_file(path, mods, [{"acc": a, "miss": 0} for a in FC_ACCURACIES])
    except (OSError, ValueError):
        return None
    if res["engine"] != LIVE:
        return None
    return res["difficulty"]["stars"], [round(s["pp"], 1) for s in res["scores"]]


def _rosu(content: bytes, mods: list[dict], scores) -> dict:
    beatmap = rosu.Beatmap(bytes=content)
    diff = rosu.Difficulty(mods=mods, lazer=True).calculate(beatmap)
    base = rosu.BeatmapAttributesBuilder(map=beatmap, mods=mods).build()
    out = []
    for s in scores:
        kw = {"misses": s.get("miss", 0)}
        if "n300" in s or "n100" in s or "n50" in s:
            kw.update(n300=s.get("n300"), n100=s.get("n100", 0), n50=s.get("n50", 0))
            if s.get("slider_tail_hit") is not None:
                kw["slider_end_hits"] = s["slider_tail_hit"]
            if s.get("large_tick_miss") is not None:
                kw["large_tick_hits"] = max(0, (diff.n_large_ticks or 0) - s["large_tick_miss"])
        else:
            kw["accuracy"] = s.get("acc", 1.0) * 100
        if s.get("combo") is not None:
            kw["combo"] = s["combo"]
        p = rosu.Performance(mods=mods, lazer=True, **{k: v for k, v in kw.items() if v is not None}).calculate(diff)
        out.append({"pp": p.pp, "aim": p.pp_aim or 0, "speed": p.pp_speed or 0, "accuracy": p.pp_accuracy or 0,
                    "flashlight": p.pp_flashlight or 0, "reading": 0.0, "acc": None, "combo": s.get("combo"),
                    "effective_miss": p.effective_miss_count})
    return {
        "engine": ROSU,
        "difficulty": {"stars": diff.stars, "aim": diff.aim, "speed": diff.speed, "reading": 0.0,
                       "max_combo": diff.max_combo, "cs": base.cs, "ar": base.ar, "od": base.od, "hp": base.hp},
        "scores": out,
    }


def score_request(row) -> dict:
    """Partie de la base -> statistiques pour le calculateur (les réelles quand on les a)."""
    stats = json.loads(row["stats"]) if row["stats"] else {}
    req = {"n300": row["n_great"], "n100": row["n_ok"], "n50": row["n_meh"], "miss": row["n_miss"],
           "combo": row["max_combo"]}
    if "slider_tail_hit" in stats:
        req["slider_tail_hit"] = stats["slider_tail_hit"]
    if "large_tick_miss" in stats:
        req["large_tick_miss"] = stats["large_tick_miss"]
    return req


def bonus_pp(ranked_maps: int) -> float:
    return BONUS_MAX * (1 - 0.995 ** min(ranked_maps, 1000))


def weighted(values: list[float]) -> float:
    """Somme pondérée d'osu! : la 1re partie compte en entier, puis ×0,95 à chaque rang."""
    return sum(v * 0.95 ** i for i, v in enumerate(values))
