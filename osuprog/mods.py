"""Gestion des mods lazer : filtrage des scores et clé de cache par combinaison."""
import json

# Mods qui changent la difficulté calculée (étoiles, AR, OD...).
DIFFICULTY_MODS = {"DT", "NC", "HT", "DC", "HR", "EZ", "FL", "DA", "TD", "HD", "CL"}
# Mods sans effet sur la difficulté, qu'on ignore dans la clé de cache.
NEUTRAL_MODS = {"NF", "SD", "PF", "MU", "SV2"}
# Mods qui rendent une partie non représentative (auto, relax, mods fun...).
ALLOWED_MODS = DIFFICULTY_MODS | NEUTRAL_MODS
# Mods qui rendent une map plus facile : une partie avec eux ne valide pas un palier.
EASY_MODS = {"EZ", "HT", "DC", "DA"}


def acronyms(mods: list[dict]) -> set[str]:
    return {m["acronym"] for m in mods}


def is_countable(mods: list[dict]) -> bool:
    """Vrai si la partie peut servir à mesurer ton niveau."""
    return acronyms(mods) <= ALLOWED_MODS


def difficulty_mods(mods: list[dict]) -> list[dict]:
    """Garde uniquement les mods utiles au calcul, triés, au format rosu-pp."""
    out = []
    for m in sorted(mods, key=lambda m: m["acronym"]):
        if m["acronym"] in DIFFICULTY_MODS:
            item = {"acronym": m["acronym"]}
            if m.get("settings"):
                item["settings"] = m["settings"]
            out.append(item)
    return out


def mods_key(mods: list[dict]) -> str:
    """Clé stable pour une combinaison de mods ("" = nomod)."""
    diff = difficulty_mods(mods)
    if not diff:
        return ""
    return json.dumps(diff, sort_keys=True, separators=(",", ":"))


def mods_label(mods: list[dict]) -> str:
    parts = []
    for m in mods:
        speed = (m.get("settings") or {}).get("speed_change")
        parts.append(f"{m['acronym']}{speed:g}x" if speed else m["acronym"])
    return "+".join(parts) if parts else "NM"
