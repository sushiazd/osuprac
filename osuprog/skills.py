"""Attributs de difficulté d'une map, classement par compétence et critères de réussite."""
from dataclasses import dataclass

import rosu_pp_py as rosu

from . import mods as modlib
from .osufile import get_osu_bytes, pattern_features

CATEGORIES = ["aim", "speed", "stamina", "accuracy", "reading", "tech"]
# À incrémenter quand classify() change : les poids en base sont alors recalculés.
CLASSIFIER_VERSION = "5"
LABELS = {
    "aim": "Aim",
    "speed": "Speed",
    "stamina": "Stamina",
    "accuracy": "Accuracy",
    "reading": "Reading",
    "tech": "Tech",
}


# À incrémenter quand les critères de réussite changent : les paliers sont alors recalculés.
CRITERIA_VERSION = "2"
ACC_RAMP = 10  # une précision jusqu'à 10 points sous l'objectif compte encore comme une réussite partielle
MISS_RAMP = 0.10  # au-delà de la limite, la réussite partielle s'éteint sur au moins 10 % des notes en miss


@dataclass(frozen=True)
class Criterion:
    min_acc: float        # précision minimale, en %
    max_miss_pct: float   # miss maximum, en % du nombre de notes de la map

    def max_miss(self, n_objects: int) -> int:
        """Miss autorisés sur une map de n_objects notes (au moins 1)."""
        return max(1, round(self.max_miss_pct / 100 * n_objects))

    def text(self, n_objects: int | None = None) -> str:
        miss = f"≤ {self.max_miss_pct:g} % de miss".replace(".", ",")
        if n_objects:
            miss += f" (≈ {self.max_miss(n_objects)} sur cette map)"
        return f"acc ≥ {self.min_acc:g} % et {miss}"


# Ce qu'il faut réussir sur une map pour considérer la compétence maîtrisée à ce niveau.
# Calibré sur de vraies parties de joueurs entre 2 et 4★ : à 3-4★ la précision médiane tourne
# autour de 80 % (rang B) et presque aucune partie ne dépasse 93 %. L'Accuracy est la plus exigeante.
# Miss resserrés le 2026-10-03 (5-6 % -> 1,5-2,5 %) : sur tes parties où la précision est atteinte, la médiane
# est à 0,65 % de miss et le 75e centile à 1,5 % ; l'ancienne limite ne bloquait que 7 parties finies sur 250.
# CRITERIA_VERSION n'est volontairement pas incrémenté : les paliers en cours sont simplement réévalués.
CRITERIA = {
    "aim": Criterion(80, 2),
    "speed": Criterion(80, 2.5),
    "stamina": Criterion(75, 2.5),
    "accuracy": Criterion(85, 1.5),
    "reading": Criterion(75, 2.5),
    "tech": Criterion(75, 2.5),
}
EXAM_CRITERION = Criterion(80, 2)


def map_attributes(beatmap_id: int, mods: list[dict]) -> dict:
    content = get_osu_bytes(beatmap_id)
    return attributes_from_content(content, mods)


def attributes_from_content(content: bytes, mods: list[dict]) -> dict:
    rosu_mods = modlib.difficulty_mods(mods)
    beatmap = rosu.Beatmap(bytes=content)
    diff = rosu.Difficulty(mods=rosu_mods, lazer=True).calculate(beatmap)
    base = rosu.BeatmapAttributesBuilder(map=beatmap, mods=rosu_mods).build()
    feats = pattern_features(content, base.clock_rate)
    return {
        "stars": round(diff.stars, 3),
        "aim": round(diff.aim or 0, 3),
        "speed": round(diff.speed or 0, 3),
        "slider_factor": round(diff.slider_factor or 1, 4),
        "speed_note_count": round(diff.speed_note_count or 0, 1),
        "max_combo": diff.max_combo,
        "ar": round(base.ar, 2),
        "od": round(base.od, 2),
        "cs": round(base.cs, 2),
        "hp": round(base.hp, 2),
        "bpm": round(beatmap.bpm * base.clock_rate, 1),
        "clock_rate": base.clock_rate,
        "hidden": "HD" in modlib.acronyms(mods),
        **feats,
    }


def ramp(x: float, lo: float, hi: float) -> float:
    """0 sous lo, 1 au-dessus de hi, linéaire entre les deux."""
    if hi == lo:
        return float(x >= hi)
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


# Valeurs médianes par niveau d'étoiles, mesurées sur le catalogue (2 925 maps ranked
# populaires de 1 à 8★, voir tools/build_pool.py). Une map se démarque dans une
# compétence quand elle s'écarte de ce qui est habituel à son niveau.
TYPICAL = {  # étoiles: (AR, OD, rapport aim/speed, slider factor)
    1.25: (3.0, 2.0, 1.65, 0.939),
    1.75: (3.0, 2.0, 1.53, 0.965),
    2.25: (5.0, 4.0, 1.39, 0.975),
    2.75: (6.25, 5.5, 1.33, 0.976),
    3.25: (7.5, 6.0, 1.28, 0.985),
    3.75: (8.0, 6.5, 1.25, 0.986),
    4.25: (8.5, 7.3, 1.24, 0.990),
    4.75: (9.0, 8.0, 1.22, 0.990),
    5.25: (9.0, 8.0, 1.25, 0.993),
    5.75: (9.2, 8.65, 1.27, 0.993),
    6.25: (9.3, 9.0, 1.29, 0.994),
    6.75: (9.5, 9.0, 1.26, 0.994),
    7.25: (9.6, 9.3, 1.24, 0.995),
    7.75: (9.8, 9.6, 1.20, 0.994),
}


def typical(stars: float, index: int) -> float:
    """Interpolation linéaire dans TYPICAL (valeurs bornées aux extrémités)."""
    points = sorted(TYPICAL.items())
    if stars <= points[0][0]:
        return points[0][1][index]
    for (s0, v0), (s1, v1) in zip(points, points[1:]):
        if stars <= s1:
            t = (stars - s0) / (s1 - s0)
            return v0[index] + t * (v1[index] - v0[index])
    return points[-1][1][index]


def expected_ar(stars: float) -> float:
    return typical(stars, 0)


def expected_od(stars: float) -> float:
    return typical(stars, 1)


def classify(a: dict) -> dict[str, float]:
    """Poids de chaque compétence dans la map (somme = 1).

    Tout est relatif au niveau d'étoiles : à 2★, un AR 7 est très haut et un
    rapport aim/speed de 1,4 est banal, alors qu'à 6★ c'est l'inverse.
    """
    stars = a["stars"]
    ratio = a["aim"] / a["speed"] if a["speed"] > 0 else 2.0
    rel = ratio / typical(stars, 2)  # > 1 : plus d'aim que d'habitude à ce niveau
    extra_sliders = (1 - a["slider_factor"]) - (1 - typical(stars, 3))
    streams = ramp(a["stream_frac"], 0.10, 0.35)
    stream_stamina = max(ramp(a["max_run"], 12, 32), ramp(a["long_stream_frac"], 0.04, 0.15))         * ramp(a["length_s"], 60, 150)
    # Sans streams (bas niveau surtout), l'endurance, c'est tenir une map longue.
    stamina = max(stream_stamina, 0.7 * ramp(a["length_s"], 180, 330))
    raw = {
        "aim": ramp(rel, 0.92, 1.12) * (1 - 0.8 * streams),
        "speed": max(streams, 1 - ramp(rel, 0.74, 0.86)) * (1 - 0.5 * stamina),
        "stamina": stamina,
        "accuracy": ramp(a["od"] - expected_od(stars), 0.25, 0.9),
        "reading": max(
            ramp(expected_ar(stars) - a["ar"], 0.7, 1.5),
            ramp(a["ar"] - expected_ar(stars), 1.0, 2.0),
            0.6 if a.get("hidden") else 0.0,
        ),
        "tech": ramp(extra_sliders, 0.015, 0.06),
    }
    # Socle aim/speed pour que chaque map compte un peu même sans trait marqué.
    lean = ramp(rel, 0.80, 1.08)
    raw["aim"] += 0.25 * lean
    raw["speed"] += 0.25 * (1 - lean)

    total = sum(raw.values())
    weights = {c: v / total for c, v in raw.items()}
    weights = {c: w for c, w in weights.items() if w >= 0.1}
    total = sum(weights.values())
    return {c: round(w / total, 3) for c, w in weights.items()}


def primary(weights: dict[str, float]) -> str:
    return max(weights, key=weights.get)


def outcome(passed: bool, acc: float, misses: int, n_objects: int, crit: Criterion) -> float:
    """Réussite graduée entre 0 et 1 : 1 = critère atteint, 0 = fail ou très loin."""
    if not passed:
        return 0.0
    acc_part = ramp(acc, crit.min_acc - ACC_RAMP, crit.min_acc)
    allowed = crit.max_miss(n_objects)
    miss_part = 1.0 if misses <= allowed else max(0.0, 1 - (misses - allowed) / max(2 * allowed, MISS_RAMP * n_objects))
    return round(min(acc_part, miss_part), 3)


def meets(passed: bool, acc: float, misses: int, n_objects: int, crit: Criterion) -> bool:
    return passed and acc >= crit.min_acc and misses <= crit.max_miss(n_objects)
