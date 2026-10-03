"""Mise en forme des nombres à la française (virgule décimale), partagée par tout le projet."""


def stars(value: float | None) -> str:
    return f"{value:.2f}★".replace(".", ",") if value is not None else "?"


def level(level_c: int) -> str:
    """Niveau stocké en centièmes d'étoile (425 -> « 4,25★ »)."""
    return stars(level_c / 100)


def pct(value: float) -> str:
    return f"{value:.2f} %".replace(".", ",")


def num(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def share(value: float) -> str:
    """0,4 -> « 40 % »."""
    return f"{value * 100:.0f} %"
