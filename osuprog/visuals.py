"""Rendu visuel du tableau de bord : morceaux de HTML/SVG à afficher avec st.html.

Fonctions pures (aucun appel à Streamlit) : faciles à tester. Les styles sont dans theme.css (classes « op-… »).
Tout texte venant des maps ou de l'API passe par esc() : un titre de map ne peut pas casser la page.
"""
import base64
import html
import math

from .fmt import num, stars as stars_text

# Spectre officiel d'osu! lazer pour la couleur des étoiles (OsuColour.ForStarDifficulty).
STAR_SPECTRUM = [
    (0.1, "#4290fb"), (1.25, "#4fc0ff"), (2.0, "#4fffd5"), (2.5, "#7cff4f"), (3.3, "#f6f05c"),
    (4.2, "#ff8068"), (4.9, "#ff4e6f"), (5.8, "#c645b8"), (6.7, "#6563de"), (7.7, "#18158e"), (9.0, "#000000"),
]

SKILL_COLORS = {
    "aim": "#ff66aa", "speed": "#66ccff", "stamina": "#ffaa33",
    "accuracy": "#5cf08f", "reading": "#aa88ff", "tech": "#4fdbc6", "exam": "#ff4f6f",
}
SKILL_SYMBOLS = {
    "aim": "my_location", "speed": "speed", "stamina": "battery_charging_full",
    "accuracy": "adjust", "reading": "visibility", "tech": "gesture", "exam": "school",
}
ROLE_STYLE = {  # rôle -> (couleur, icône Material Symbols)
    "Échauffement": ("#ffaa33", "local_fire_department"),
    "Test de niveau": ("#aa88ff", "straighten"),
    "Objectif": ("#ff66aa", "flag"),
    "Entraînement": ("#66ccff", "fitness_center"),
    "Examen": ("#ff4f6f", "school"),
    "Défi": ("#ffd23f", "bolt"),
    "Révision": ("#4fdbc6", "replay"),
}
STATE_STYLE = {  # état d'une map du programme -> (classe, icône)
    "done": ("op-state-done", "check_circle"),
    "retry": ("op-state-retry", "replay"),
    "todo": ("op-state-todo", "radio_button_unchecked"),
}
BADGE_TIERS = ["bronze", "silver", "gold", "platinum", "mythic"]


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def icon(name: str, cls: str = "") -> str:
    return f'<span class="op-icon {cls}">{esc(name)}</span>'


# --- Couleurs ------------------------------------------------------------------------------------------

def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def star_color(stars: float | None) -> str:
    """Couleur d'une difficulté, exactement comme dans osu! lazer (interpolation dans le spectre)."""
    if stars is None:
        return "#8d80a3"
    if stars <= STAR_SPECTRUM[0][0]:
        return STAR_SPECTRUM[0][1]
    for (s0, c0), (s1, c1) in zip(STAR_SPECTRUM, STAR_SPECTRUM[1:]):
        if stars <= s1:
            t = (stars - s0) / (s1 - s0)
            r = [round(a + t * (b - a)) for a, b in zip(_hex(c0), _hex(c1))]
            return "#{:02x}{:02x}{:02x}".format(*r)
    return STAR_SPECTRUM[-1][1]


def star_pill(stars: float | None, big: bool = False) -> str:
    """Pastille d'étoiles colorée ; texte sombre sur les couleurs claires, doré sur les très hautes (comme lazer)."""
    color = star_color(stars)
    text = "#ffd966" if stars is not None and stars >= 6.5 else "#1b1424"
    return (f'<span class="op-star{" op-star-big" if big else ""}" style="background:{color};color:{text};'
            f'--glow:{color}">{esc(stars_text(stars))}</span>')


def chip(label: str, color: str, symbol: str | None = None, solid: bool = False) -> str:
    style = f"--c:{color}"
    return (f'<span class="op-chip{" op-chip-solid" if solid else ""}" style="{style}">'
            f'{icon(symbol) if symbol else ""}{esc(label)}</span>')


def skill_chip(cat: str, label: str) -> str:
    return chip(label, SKILL_COLORS.get(cat, "#8d80a3"), SKILL_SYMBOLS.get(cat))


def role_chip(role: str) -> str:
    color, symbol = ROLE_STYLE.get(role, ("#8d80a3", "label"))
    return chip(role, color, symbol, solid=True)


def ring(p: float | None, size: int = 64, label: str | None = None, color: str = "#ff66aa",
         caption: str | None = None) -> str:
    """Anneau de pourcentage (chances, progression), en CSS pur : le HTML de Streamlit n'accepte pas le SVG."""
    if p is None:
        return ""
    p = max(0.0, min(1.0, p))
    text = label if label is not None else f"{round(p * 100)}%"
    cap = f'<div class="op-ring-cap">{esc(caption)}</div>' if caption else ""
    return (f'<div class="op-ring" style="--c:{color};--p:{p * 100:.1f};--s:{size}px">'
            f'<div class="op-ring-disc"></div>'
            f'<div class="op-ring-label" style="font-size:{max(11, size // 4)}px">{esc(text)}</div>{cap}</div>')


def bar(p: float, color: str = "#ff66aa", height: int = 8) -> str:
    p = max(0.0, min(1.0, p))
    return (f'<div class="op-bar" style="height:{height}px"><div class="op-bar-fill" '
            f'style="width:{p * 100:.1f}%;--c:{color}"></div></div>')


# --- Bandeau du joueur ---------------------------------------------------------------------------------

def player_hero(user: dict, streak: dict, clear: dict | None, rank_c: int, target_c: int,
                played: int, total: int, goals_met: int, goals: int, minutes: float) -> str:
    rank = f"#{user['global_rank']:,}".replace(",", " ") if user.get("global_rank") else "non classé"
    avatar = (f'<img class="op-avatar" src="{esc(user.get("avatar_url"))}" alt="">' if user.get("avatar_url")
              else f'<div class="op-avatar op-avatar-empty">{icon("person")}</div>')
    flame = streak.get("current", 0)
    chips = [chip(f"{flame} jour{'s' if flame > 1 else ''} d'affilée", "#ffaa33", "local_fire_department", solid=True)
             if flame else chip("Lance ta série aujourd'hui", "#ffaa33", "local_fire_department")]
    if clear:
        chips.append(f'<span class="op-hero-stat">Tu finis jusqu\'à {star_pill(clear["level"])}</span>')
    chips.append(f'<span class="op-hero-stat">Niveau {star_pill(rank_c / 100)} '
                 f'{icon("arrow_forward", "op-muted")} {star_pill(target_c / 100)}</span>')
    session = ring(played / total if total else 0, size=112, label=f"{played}/{total}", color="#ff66aa",
                   caption="maps jouées")
    goal_ring = ring(goals_met / goals if goals else 0, size=112, label=f"{goals_met}/{goals}", color="#66ccff",
                     caption="objectifs") if goals else ""
    return f"""
<div class="op-hero">
  <div class="op-hero-tris"></div>
  <div class="op-hero-main">
    <div class="op-avatar-wrap">{avatar}</div>
    <div class="op-hero-text">
      <div class="op-kicker">{icon("today")} Séance du jour · ≈ {minutes:.0f} min</div>
      <div class="op-hero-name">{esc(user.get("username", "Joueur"))}</div>
      <div class="op-hero-sub">{(user.get("pp") or 0):.0f} pp · {esc(rank)}</div>
      <div class="op-hero-chips">{"".join(chips)}</div>
    </div>
  </div>
  <div class="op-hero-rings">{session}{goal_ring}</div>
</div>"""


def focus_strip(shares: dict, labels: dict, ratings: dict) -> str:
    """Les compétences travaillées aujourd'hui, avec leur part du programme."""
    items = []
    for s in sorted(shares, key=shares.get, reverse=True)[:4]:
        if s not in labels:
            continue
        items.append(
            f'<div class="op-focus" style="--c:{SKILL_COLORS[s]}">'
            f'<div class="op-focus-top">{icon(SKILL_SYMBOLS[s])}<b>{esc(labels[s])}</b>'
            f'<span class="op-focus-share">{shares[s] * 100:.0f}%</span></div>'
            f'{bar(shares[s] / max(shares.values()), SKILL_COLORS[s], 6)}'
            f'<div class="op-focus-note">note {esc(stars_text(ratings[s]["rating"]))}</div></div>')
    return f'<div class="op-focus-row">{"".join(items)}</div>'


# --- Cartes de map ----------------------------------------------------------------------------------------

def cover_url(set_id, kind: str = "cover") -> str | None:
    return f"https://assets.ppy.sh/beatmaps/{int(set_id)}/covers/{kind}.jpg" if set_id else None


def map_card(info: dict, *, number: int | None, role: str | None, cat: str, skill_label: str,
             state: dict | None, reason: str, goal: str, p: float | None, minutes: float | None,
             hero: bool = False, hp_note: str = "") -> str:
    """Carte d'une map façon sélection de chanson : bannière de la map, rôle, étoiles, chances et statut."""
    cover = cover_url(info.get("set_id"), "cover" if hero else "card")
    bg = f"background-image:linear-gradient(90deg,rgba(14,10,21,.97) 0%,rgba(14,10,21,.86) 45%,rgba(14,10,21,.45) 80%,rgba(14,10,21,.75) 100%),url('{cover}')" if cover else ""
    accent = ROLE_STYLE.get(role, (SKILL_COLORS.get(cat, "#8d80a3"), ""))[0] if role else SKILL_COLORS.get(cat, "#8d80a3")
    attrs = info.get("attrs") or {}
    facts = []
    if minutes:
        facts.append(f'{icon("schedule")}≈ {minutes:.0f} min')
    if attrs.get("length_s"):
        m, s = divmod(int(attrs["length_s"]), 60)
        facts.append(f'{icon("timer")}{m}:{s:02d}')
    if attrs.get("bpm"):
        facts.append(f'{icon("music_note")}{attrs["bpm"]:.0f} BPM')
    for key in ("ar", "od", "hp"):
        if attrs.get(key) is not None:
            facts.append(f'<b>{key.upper()}</b> {esc(num(attrs[key]))}')
    status = ""
    if state:
        cls, sym = STATE_STYLE.get(state["state"], STATE_STYLE["todo"])
        status = f'<div class="op-status {cls}">{icon(sym)}{esc(state["label"])}</div>'
    show_p = p is not None and role not in ("Échauffement", "Test de niveau")
    chance = ring(p, size=84 if hero else 62, color=accent, caption="chances") if show_p else ""
    number_html = f'<div class="op-num">{number}</div>' if number is not None else ""
    return f"""
<div class="op-map{" op-map-hero" if hero else ""}" style="--accent:{accent};{bg}">
  {number_html}
  <div class="op-map-body">
    <div class="op-map-chips">{role_chip(role) if role else ""}{skill_chip(cat, skill_label)}{star_pill(info.get("stars"), big=hero)}</div>
    <div class="op-map-title">{esc(info.get("title"))}</div>
    <div class="op-map-artist">{esc(info.get("artist"))} · <i>{esc(info.get("version"))}</i></div>
    <div class="op-map-facts">{"".join(f"<span>{f}</span>" for f in facts)}</div>
    <div class="op-map-reason">{esc(reason)}{(" " + esc(hp_note)) if hp_note else ""}</div>
    <div class="op-map-goal">{icon("target")}{esc(goal)}</div>
    {status}
  </div>
  <div class="op-map-side">{chance}</div>
</div>"""


# --- Hexagone des compétences ---------------------------------------------------------------------------------

def svg_image(svg: str, cls: str = "", alt: str = "") -> str:
    """Un SVG affiché comme image (data URI) : seule façon d'afficher du SVG dans st.html."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" alt="{esc(alt)}" src="data:image/svg+xml;base64,{data}">'


def radar(skills: list[str], labels: dict, ratings: dict, sds: dict, clear_level: float | None = None,
          size: int = 440) -> str:
    """Hexagone des compétences : ta note sur chaque axe, sa marge d'erreur, et le niveau où tu finis les maps.

    La forme est une image SVG ; les noms et les notes sont du HTML posé par-dessus (police du thème)."""
    values = [ratings[s] for s in skills]
    ref = [clear_level] if clear_level else []
    lo = math.floor((min(values + ref) - 0.6) * 2) / 2
    hi = math.ceil((max(values + ref) + 0.3) * 2) / 2
    cx = cy = size / 2
    R = size / 2 - 72
    n = len(skills)

    def angle(i: int) -> float:
        return -math.pi / 2 + i * 2 * math.pi / n

    def point(i: int, value: float) -> tuple[float, float]:
        r = R * max(0.0, min(1.15, (value - lo) / (hi - lo)))
        return cx + r * math.cos(angle(i)), cy + r * math.sin(angle(i))

    def poly(vals) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, v) for i, v in enumerate(vals)))

    parts = ['<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff66aa" '
             'stop-opacity=".6"/><stop offset="1" stop-color="#8866ff" stop-opacity=".5"/></linearGradient></defs>']
    steps = round((hi - lo) / 0.5)
    for k in range(1, steps + 1):  # anneaux tous les 0,5★
        fill = "rgba(255,255,255,.03)" if k % 2 else "none"
        parts.append(f'<polygon points="{poly([lo + 0.5 * k] * n)}" fill="{fill}" stroke="rgba(255,255,255,.10)" '
                     'stroke-width="1"/>')
    for i in range(n):
        x, y = point(i, hi)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(255,255,255,.08)"/>')
    if clear_level:
        parts.append(f'<polygon points="{poly([clear_level] * n)}" fill="none" stroke="#ffd23f" stroke-width="1.8" '
                     'stroke-dasharray="6 6" opacity=".85"/>')
    parts.append(f'<polygon points="{poly([ratings[s] + sds[s] for s in skills])}" fill="rgba(255,102,170,.10)" '
                 'stroke="rgba(255,102,170,.45)" stroke-width="1" stroke-dasharray="2 4"/>')
    parts.append(f'<polygon points="{poly(values)}" fill="url(#g)" stroke="#ff66aa" stroke-width="3" '
                 'stroke-linejoin="round"/>')
    for i, s in enumerate(skills):
        x, y = point(i, ratings[s])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{SKILL_COLORS[s]}" stroke="#0e0a15" '
                     'stroke-width="3"/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{"".join(parts)}</svg>'

    overlay = []
    for i, s in enumerate(skills):
        a = angle(i)
        lx, ly = cx + (R + 48) * math.cos(a), cy + (R + 42) * math.sin(a)
        overlay.append(
            f'<div class="op-radar-label" style="left:{lx / size * 100:.2f}%;top:{ly / size * 100:.2f}%;'
            f'--c:{SKILL_COLORS[s]}">{icon(SKILL_SYMBOLS[s])}<b>{esc(labels[s])}</b>'
            f'<span>{esc(stars_text(ratings[s]))}</span></div>')
    for k in range(1, steps + 1, 2):
        level = lo + 0.5 * k
        x, y = point(0, level)
        overlay.append(f'<div class="op-radar-tick" style="left:{(x + 8) / size * 100:.2f}%;'
                       f'top:{y / size * 100:.2f}%">{esc(num(level))}★</div>')
    clear_legend = (f'<span><i class="op-lg op-lg-clear"></i>tu finis jusqu’à {esc(stars_text(clear_level))}</span>'
                    if clear_level else "")
    legend = (f'<div class="op-radar-legend"><span><i class="op-lg op-lg-shape"></i>tes notes</span>'
              f'<span><i class="op-lg op-lg-band"></i>marge d’erreur</span>{clear_legend}</div>')
    return (f'<div class="op-radar"><div class="op-radar-box" style="max-width:{size}px">'
            f'{svg_image(svg, "op-radar-img", "Hexagone de tes compétences")}{"".join(overlay)}</div>{legend}</div>')


def skill_tile(cat: str, label: str, rating: float, margin: float, change: float | None, badge: str | None,
               significant: bool, gap: float) -> str:
    color = SKILL_COLORS[cat]
    delta = ""
    if change is not None and abs(change) >= 0.01:
        up = change > 0
        delta = (f'<span class="op-delta {"op-up" if up else "op-down"}">'
                 f'{icon("trending_up" if up else "trending_down")}{change:+.2f}★ en 7 j</span>').replace(".", ",")
    note = ("écart net" if significant else "dans la marge") + f" ({gap:+.2f}★)".replace(".", ",")
    margin_text = f"±{margin:.2f}".replace(".", ",")
    badge_html = f'<span class="op-skill-badge">{esc(badge)}</span>' if badge else ""
    return (f'<div class="op-skill" style="--c:{color}">'
            f'<div class="op-skill-head">{icon(SKILL_SYMBOLS[cat])}<b>{esc(label)}</b>{badge_html}</div>'
            f'<div class="op-skill-value">{esc(stars_text(rating))}<small>{margin_text}</small></div>'
            f'<div class="op-skill-foot">{delta}<span class="op-muted">{esc(note)}</span></div></div>')


def skill_grid(tiles: list[str]) -> str:
    return f'<div class="op-skill-grid">{"".join(tiles)}</div>'


# --- Paliers ---------------------------------------------------------------------------------------------------

def tier_track(cat: str, label: str, current_c: int, step_c: int, met: int, needed: int) -> str:
    """Piste de paliers façon passe de combat : validés, en cours (avec ses maps réussies), à venir."""
    color = SKILL_COLORS[cat]
    nodes = []
    shown = [k for k in range(-2, 4) if current_c + k * step_c > 0]
    fill = shown.index(0) / (len(shown) - 1) * 100 if len(shown) > 1 else 0
    for k in shown:
        level = current_c + k * step_c
        kind = "done" if k < 0 else "current" if k == 0 else "locked"
        inner = (icon("check", "op-icon-fill") if kind == "done" else f"{met}/{needed}" if kind == "current"
                 else icon("lock"))
        nodes.append(f'<div class="op-node op-node-{kind}"><div class="op-node-dot">{inner}</div>'
                     f'<div class="op-node-label">{esc(stars_text(level / 100))}</div></div>')
    return (f'<div class="op-track" style="--c:{color};--fill:{fill:.0f}%"><div class="op-track-name">{icon(SKILL_SYMBOLS[cat])}'
            f'<b>{esc(label)}</b></div><div class="op-track-line">{"".join(nodes)}</div></div>')


def rank_emblem(rank_c: int, target_c: int, ready: int, total: int) -> str:
    return f"""
<div class="op-rank">
  <div class="op-rank-hex"><div class="op-rank-inner"><small>niveau</small><b>{esc(num(rank_c / 100))}</b><span>★</span></div></div>
  <div class="op-rank-text">
    <div class="op-kicker">{icon("military_tech")} Niveau validé</div>
    <div class="op-rank-title">Prochain : {star_pill(target_c / 100, big=True)}</div>
    <div class="op-rank-sub">{ready}/{total} compétences prêtes pour l'examen</div>
    {bar(ready / total if total else 0, "#ff66aa", 10)}
  </div>
</div>"""


# --- Succès --------------------------------------------------------------------------------------------------------

def achievement_badge(title: str, text: str, symbol: str, level: int, unlocked: str | None, progress: float) -> str:
    tier = BADGE_TIERS[min(level, len(BADGE_TIERS) - 1)]
    state = "op-badge-on" if unlocked else "op-badge-off"
    foot = (f'<div class="op-badge-date">{icon("event")}{esc(unlocked)}</div>' if unlocked
            else bar(progress, "#ff66aa", 6) + f'<div class="op-badge-date">{progress * 100:.0f}%</div>'
            if progress > 0 else '<div class="op-badge-date">à débloquer</div>')
    return (f'<div class="op-badge {state} op-tier-{tier}"><div class="op-badge-hex">{icon(symbol, "op-icon-fill")}</div>'
            f'<div class="op-badge-title">{esc(title)}</div><div class="op-badge-text">{esc(text)}</div>{foot}</div>')


def badge_grid(badges: list[str]) -> str:
    return f'<div class="op-badge-grid">{"".join(badges)}</div>'


def streak_hero(current: int, best: int, played_today: bool) -> str:
    hint = "Série prolongée aujourd'hui !" if played_today else ("Joue une map aujourd'hui pour la garder"
                                                                  if current else "Une map par jour suffit")
    return f"""
<div class="op-streak">
  <div class="op-flame">{icon("local_fire_department", "op-icon-fill")}</div>
  <div><div class="op-streak-num">{current}<small> jour{"s" if current > 1 else ""}</small></div>
  <div class="op-streak-sub">d'affilée · record {best} · {esc(hint)}</div></div>
</div>"""


def stat_tiles(items: list[tuple[str, str, str, str]]) -> str:
    """Tuiles de chiffres : (icône, valeur, libellé, couleur)."""
    tiles = "".join(f'<div class="op-tile" style="--c:{c}">{icon(sym)}<div class="op-tile-value">{esc(v)}</div>'
                    f'<div class="op-tile-label">{esc(label)}</div></div>' for sym, v, label, c in items)
    return f'<div class="op-tiles">{tiles}</div>'


def page_header(symbol: str, title: str, subtitle: str = "", color: str = "#ff66aa") -> str:
    return (f'<div class="op-page-head" style="--c:{color}"><div class="op-page-icon">{icon(symbol, "op-icon-fill")}</div>'
            f'<div><h1 class="op-page-title">{esc(title)}</h1>'
            f'{f"<div class=op-page-sub>{esc(subtitle)}</div>" if subtitle else ""}</div></div>')
