"""Analyse des replays de lazer : régularité au clic (unstable rate), tendance à taper en avance ou en retard,
et nature des miss (visée ou rythme).

Lazer garde un replay .osr pour chaque partie enregistrée sur ce PC. On rejoue ses images (position du curseur
et touches) contre les notes de la map, avec les fenêtres de précision d'osu!. C'est une reconstitution
simplifiée du jeu (pas de décalage des notes empilées, sliders jugés sur leur tête) : les chiffres sont fiables
en tendance, pas à la note près. `check_counts` compare les miss reconstitués aux vrais pour le vérifier.
"""
import lzma
import statistics
import struct
from dataclasses import dataclass, field

ANALYSIS_VERSION = 1
KEY_MASK = 1 | 2          # M1 et M2 (K1 et K2 les activent aussi)
SEED_FRAME = -12345        # image technique en fin de replay


# --- Lecture du fichier .osr ------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        value = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += size
        return value[0] if len(value) == 1 else value

    def uleb(self) -> int:
        result = shift = 0
        while True:
            byte = self.data[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7

    def string(self) -> str:
        if self.data[self.pos] == 0x00:
            self.pos += 1
            return ""
        self.pos += 1  # 0x0b
        n = self.uleb()
        text = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
        self.pos += n
        return text


@dataclass
class Replay:
    mode: int
    beatmap_md5: str
    counts: dict
    mods: int
    frames: list = field(default_factory=list)   # (temps absolu ms, x, y, touches)


def parse_osr(data: bytes) -> Replay:
    r = _Reader(data)
    mode = r.take("B")
    r.take("i")                      # version du jeu
    beatmap_md5 = r.string()
    r.string()                       # joueur
    r.string()                       # empreinte du replay
    n300, n100, n50, geki, katu, miss = (r.take("H") for _ in range(6))
    r.take("i")                      # score
    r.take("H")                      # combo max
    r.take("B")                      # perfect
    mods = r.take("i")
    r.string()                       # barre de vie
    r.take("q")                      # date
    length = r.take("i")
    raw = lzma.decompress(data[r.pos:r.pos + length], format=lzma.FORMAT_ALONE).decode("ascii", "replace")
    frames, t = [], 0
    for chunk in raw.split(","):
        parts = chunk.split("|")
        if len(parts) != 4:
            continue
        w = int(float(parts[0]))
        if w == SEED_FRAME:
            continue
        t += w
        frames.append((t, float(parts[1]), float(parts[2]), int(float(parts[3]))))
    return Replay(mode, beatmap_md5, {"300": n300, "100": n100, "50": n50, "miss": miss}, mods, frames)


# --- Notes de la map ------------------------------------------------------------------------------

def parse_map(content: bytes) -> dict:
    """Ce qu'il faut pour juger les clics : CS, OD, empilement et notes (temps, x, y, type)."""
    text = content.decode("utf-8", "replace")
    section, diff, objects = "", {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
            continue
        if section == "[Difficulty]" and ":" in line:
            key, _, value = line.partition(":")
            diff[key.strip()] = float(value)
        elif section == "[HitObjects]" and line:
            p = line.split(",")
            if len(p) < 4:
                continue
            flags = int(p[3])
            kind = 3 if flags & 8 else 1 if flags & 2 else 0
            objects.append((int(float(p[2])), float(p[0]), float(p[1]), kind))
    objects.sort()
    return {"cs": diff.get("CircleSize", 5.0), "od": diff.get("OverallDifficulty", 5.0), "objects": objects}


def hit_windows(od: float) -> tuple[float, float, float]:
    """Fenêtres (±ms) des 300, 100 et 50 d'osu!standard."""
    return 80 - 6 * od, 140 - 8 * od, 200 - 10 * od


def apply_mods(beatmap: dict, acronyms: set[str]) -> tuple[dict, float]:
    """CS/OD et vitesse avec les mods (HR, EZ, DT, HT). Renvoie (map ajustée, vitesse de lecture)."""
    cs, od = beatmap["cs"], beatmap["od"]
    if "HR" in acronyms:
        cs, od = min(10.0, cs * 1.3), min(10.0, od * 1.4)
    if "EZ" in acronyms:
        cs, od = cs * 0.5, od * 0.5
    rate = 1.5 if acronyms & {"DT", "NC"} else 0.75 if acronyms & {"HT", "DC"} else 1.0
    return {**beatmap, "cs": cs, "od": od}, rate


# --- Jugement des clics -----------------------------------------------------------------------------

@dataclass
class Analysis:
    errors: list            # écart (ms, temps réel) de chaque note touchée : négatif = en avance
    great: int
    ok: int
    meh: int
    miss_aim: int           # clic au bon moment, mais à côté de la note
    miss_timing: int        # aucun clic au bon moment (trop tôt, trop tard ou oublié)
    judged: int             # notes jugées (jusqu'à la fin du replay : un fail s'arrête avant)

    @property
    def misses(self) -> int:
        return self.miss_aim + self.miss_timing

    @property
    def unstable_rate(self) -> float | None:
        return round(10 * statistics.pstdev(self.errors), 1) if len(self.errors) >= 20 else None

    @property
    def mean_error(self) -> float | None:
        return round(statistics.fmean(self.errors), 1) if len(self.errors) >= 20 else None


def presses(frames: list) -> list[tuple[float, float, float]]:
    """Instants où une touche est enfoncée (front montant), avec la position du curseur."""
    out, prev = [], 0
    for t, x, y, keys in frames:
        keys &= KEY_MASK
        if keys & ~prev:
            out.append((t, x, y))
        prev = keys
    return out


def analyse(replay: Replay, beatmap: dict, acronyms: set[str]) -> Analysis:
    """Rejoue les clics contre les notes. Chaque clic ne peut toucher que la plus ancienne note encore en jeu
    (comme dans osu!) ; un clic hors de toutes les fenêtres est ignoré."""
    bm, rate = apply_mods(beatmap, acronyms)
    w300, w100, w50 = hit_windows(bm["od"])
    radius = 54.4 - 4.48 * bm["cs"]
    flip = "HR" in acronyms
    objs = [o for o in bm["objects"] if o[3] != 3]  # les spinners ne se jugent pas au clic
    end = replay.frames[-1][0] if replay.frames else 0
    objs = [o for o in objs if o[0] <= end]          # un fail arrête le replay
    clicks = [(t, x, 384 - y if flip else y) for t, x, y in presses(replay.frames)]

    errors, great, ok, meh, miss_aim, miss_timing = [], 0, 0, 0, 0, 0
    near_miss = [False] * len(objs)  # un clic a eu lieu dans la fenêtre, mais à côté
    i = 0
    for t, x, y in clicks:
        while i < len(objs) and t > objs[i][0] + w50:  # notes passées sans être touchées
            miss_aim, miss_timing = (miss_aim + 1, miss_timing) if near_miss[i] else (miss_aim, miss_timing + 1)
            i += 1
        if i >= len(objs):
            break
        ot, ox, oy, _ = objs[i]
        delta = t - ot
        if delta < -w50:
            continue  # trop tôt pour cette note : ignoré
        if (x - ox) ** 2 + (y - oy) ** 2 > radius ** 2:
            near_miss[i] = True
            continue
        err = abs(delta)
        great, ok, meh = (great + 1, ok, meh) if err <= w300 else (great, ok + 1, meh) if err <= w100 else (great, ok, meh + 1)
        errors.append(delta / rate)
        i += 1
    for j in range(i, len(objs)):
        miss_aim, miss_timing = (miss_aim + 1, miss_timing) if near_miss[j] else (miss_aim, miss_timing + 1)
    return Analysis(errors, great, ok, meh, miss_aim, miss_timing, len(objs))


# --- Synthèse pour le tableau de bord ---------------------------------------------------------------------

def summary(conn, recent: int = 30) -> dict | None:
    """Tes `recent` dernières parties analysées, comparées aux `recent` d'avant."""
    rows = conn.execute(
        """SELECT r.*, s.ended_at FROM replay_stats r JOIN scores s ON s.score_id = r.score_id
           WHERE r.ur IS NOT NULL ORDER BY s.ended_at DESC"""
    ).fetchall()
    if len(rows) < 5:
        return None
    last, before = rows[:recent], rows[recent:2 * recent]
    means = [r["mean_error"] for r in last]
    bias = statistics.fmean(means)
    se = statistics.stdev(means) / len(means) ** 0.5 if len(means) > 1 else float("inf")
    aim = sum(r["miss_aim"] for r in last)
    timing = sum(r["miss_timing"] for r in last)
    return {
        "n": len(last),
        "ur": statistics.median(r["ur"] for r in last),
        "ur_before": statistics.median(r["ur"] for r in before) if len(before) >= 5 else None,
        "bias": bias,
        "bias_clear": abs(bias) >= 5 and abs(bias) > 2 * se,   # même tendance d'une partie à l'autre
        "miss_aim": aim,
        "miss_timing": timing,
        "history": [(r["ended_at"], r["ur"], r["mean_error"]) for r in rows],
    }
