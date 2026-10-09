"""Rendu des composants maison du tableau de bord (HTML pour st.html). Fonctions pures, faciles à tester.

Style : sobre, une seule couleur d'accent (theme.css, classes « op-… »). Tout texte venant des maps ou de l'API
passe par esc(). Le HTML de Streamlit n'accepte pas le SVG intégré : l'hexagone est une image en data URI.
"""
import base64
import html
import math

from .fmt import stars as stars_text

ACCENT = "#ff5fa2"
DEFAULT_AVATAR_MARKERS = ("avatar-guest",)  # image par défaut d'osu! quand le joueur n'a pas mis de photo


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def icon(name: str, cls: str = "") -> str:
    return f'<span class="op-icon {cls}">{esc(name)}</span>'


def star(value: float | None) -> str:
    return f"★ {esc(stars_text(value).rstrip('★'))}"


def length(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def progress(p: float) -> str:
    return f'<div class="op-progress"><div style="width:{max(0.0, min(1.0, p)) * 100:.1f}%"></div></div>'


# --- Joueur -------------------------------------------------------------------------------------------

def has_photo(user: dict) -> bool:
    url = user.get("avatar_url") or ""
    return bool(url) and not any(m in url for m in DEFAULT_AVATAR_MARKERS)


def avatar(user: dict, size: int = 40) -> str:
    """La photo de profil osu!, ou l'initiale du pseudo si le joueur n'en a pas mis."""
    style = f"width:{size}px;height:{size}px"
    if has_photo(user):
        return f'<img class="op-avatar" style="{style}" src="{esc(user["avatar_url"])}" alt="">'
    initial = (user.get("username") or "?")[:1].upper()
    return f'<div class="op-avatar op-monogram" style="{style};font-size:{size * 0.42:.0f}px">{esc(initial)}</div>'


def user_card(user: dict) -> str:
    rank = f"#{user['global_rank']:,}".replace(",", " ") if user.get("global_rank") else "non classé"
    return (f'<div class="op-user">{avatar(user)}<div><div class="op-user-name">{esc(user.get("username"))}</div>'
            f'<div class="op-user-sub">{(user.get("pp") or 0):.0f} pp · {esc(rank)}</div></div></div>')


def summary(items: list[tuple[str, str]]) -> str:
    """Une ligne de chiffres clés : [(valeur, libellé)]."""
    return '<div class="op-summary">' + "".join(f"<span><b>{esc(v)}</b>{esc(label)}</span>" for v, label in items) + "</div>"


# --- Maps ------------------------------------------------------------------------------------------------

def cover_url(set_id, kind: str = "cover") -> str | None:
    return f"https://assets.ppy.sh/beatmaps/{int(set_id)}/covers/{kind}.jpg" if set_id else None


def role_label(role: str | None, reason: str = "") -> str:
    if not role:
        return ""
    cls = "op-label op-accent" if role in ("Objectif", "Examen") else "op-label"
    tip = f' title="{esc(reason)}"' if reason else ""
    return f'<span class="{cls}"{tip}>{esc(role)}</span>'


def now_card(info: dict, *, role: str | None, skill_label: str, goal: str, p: float | None,
             minutes: float | None, reason: str = "", fc_pp: float | None = None) -> str:
    """La map à jouer maintenant : bannière, titre, quelques infos, et l'objectif."""
    cover = cover_url(info.get("set_id"))
    attrs = info.get("attrs") or {}
    meta = [f"<span><b>{star(info.get('stars'))}</b></span>", f"<span>{esc(skill_label)}</span>"]
    if attrs.get("length_s"):
        meta.append(f"<span>{icon('timer')}{length(attrs['length_s'])}</span>")
    if minutes:
        meta.append(f"<span>≈ {minutes:.0f} min</span>")
    if p is not None and role not in ("Échauffement", "Test de niveau"):
        meta.append(f"<span>{round(p * 100)} % de chances {'de la finir' if role in ('Défi', 'Montée') else 'de réussir'}</span>")
    if fc_pp:
        meta.append(f"<span>≈ {fc_pp:.0f} pp en FC</span>")
    cover_html = f'<div class="op-now-cover" style="background-image:url(\'{cover}\')"></div>' if cover else ""
    reason_html = f'<div class="op-now-sub" style="margin-top:10px">{esc(reason)}</div>' if reason else ""
    return f"""
<div class="op-now">{cover_html}
  <div class="op-now-body">
    {role_label(role)}
    <div class="op-now-title">{esc(info.get("title"))}</div>
    <div class="op-now-sub">{esc(info.get("artist"))} · {esc(info.get("version"))}</div>
    <div class="op-meta">{"".join(meta)}</div>
    {reason_html}
    <div class="op-goal"><span class="op-label">Objectif</span>{esc(goal)}</div>
  </div>
</div>"""


def map_row(info: dict, *, number: int | None, role: str | None, state: str = "todo",
            p: float | None = None, reason: str = "") -> str:
    """Une map dans une liste : vignette, titre, difficulté, rôle et statut."""
    thumb = cover_url(info.get("set_id"), "list")
    thumb_html = (f'<img class="op-thumb" src="{thumb}" alt="">' if thumb else '<div class="op-thumb"></div>')
    status = {"done": icon("check_circle", "op-check"), "retry": icon("replay", "op-retry")}.get(state, "")
    chance = (f'<span title="Chances {"de la finir" if role in ("Défi", "Montée") else "de réussir l&#39;objectif"}">'
              f"{round(p * 100)} %</span>" if p is not None and state == "todo"
              and role not in ("Échauffement", "Test de niveau") else "")
    num_html = f'<div class="op-num">{number}</div>' if number is not None else ""
    return (f'<div class="op-row{" op-done" if state == "done" else ""}">{num_html}{thumb_html}'
            f'<div class="op-row-text"><div class="op-row-title">{esc(info.get("title"))}</div>'
            f'<div class="op-row-sub">{esc(info.get("version"))} · {esc(info.get("artist"))}</div></div>'
            f'<div class="op-row-side">{role_label(role, reason)}{chance}<span>{star(info.get("stars"))}</span>'
            f'{status}</div></div>')


def pp_row(m: dict, sub: str = "", tip: str = "") -> str:
    """Une map de la chasse aux pp : vignette, titre [difficulté], et ce qu'elle rapporte en full combo."""
    thumb = cover_url(m.get("set_id"), "list")
    thumb_html = (f'<img class="op-thumb" src="{thumb}" alt="">' if thumb else '<div class="op-thumb"></div>')
    side = [f"<span><b>{m['pp']:.0f} pp</b></span>", f"<span>{star(m.get('stars'))}</span>"]
    if m.get("length_s"):
        side.append(f"<span>{icon('timer')}{length(m['length_s'])}</span>")
    return (f'<div class="op-row" title="{esc(tip)}">{thumb_html}'
            f'<div class="op-row-text"><div class="op-row-title">{esc(m.get("title"))} [{esc(m.get("version"))}]</div>'
            f'<div class="op-row-sub">{esc(sub or m.get("artist"))}</div></div>'
            f'<div class="op-row-side">{"".join(side)}</div></div>')


# --- Compétences -------------------------------------------------------------------------------------

def svg_image(svg: str, cls: str = "", alt: str = "") -> str:
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" alt="{esc(alt)}" src="data:image/svg+xml;base64,{data}">'


def radar(skills: list[str], labels: dict, ratings: dict, size: int = 380) -> str:
    """Hexagone des compétences, en une seule couleur. Les noms et notes sont posés en HTML par-dessus."""
    values = [ratings[s] for s in skills]
    lo = math.floor((min(values) - 0.6) * 2) / 2
    hi = math.ceil((max(values) + 0.3) * 2) / 2
    cx = cy = size / 2
    R = size / 2 - 64
    n = len(skills)

    def angle(i: int) -> float:
        return -math.pi / 2 + i * 2 * math.pi / n

    def point(i: int, value: float) -> tuple[float, float]:
        r = R * max(0.0, min(1.1, (value - lo) / (hi - lo)))
        return cx + r * math.cos(angle(i)), cy + r * math.sin(angle(i))

    def poly(vals) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, v) for i, v in enumerate(vals)))

    parts = [f'<polygon points="{poly([lo + 0.5 * k] * n)}" fill="none" stroke="#2a2a31" stroke-width="1"/>'
             for k in range(1, round((hi - lo) / 0.5) + 1)]
    parts += [f'<line x1="{cx}" y1="{cy}" x2="{point(i, hi)[0]:.1f}" y2="{point(i, hi)[1]:.1f}" stroke="#2a2a31"/>'
              for i in range(n)]
    parts.append(f'<polygon points="{poly(values)}" fill="{ACCENT}" fill-opacity=".14" stroke="{ACCENT}" '
                 'stroke-width="2" stroke-linejoin="round"/>')
    parts += [f'<circle cx="{point(i, v)[0]:.1f}" cy="{point(i, v)[1]:.1f}" r="3.5" fill="{ACCENT}"/>'
              for i, v in enumerate(values)]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{"".join(parts)}</svg>'
    labels_html = []
    for i, s in enumerate(skills):
        lx, ly = cx + (R + 36) * math.cos(angle(i)), cy + (R + 30) * math.sin(angle(i))
        labels_html.append(f'<div class="op-radar-label" style="left:{lx / size * 100:.2f}%;top:{ly / size * 100:.2f}%">'
                           f'<b>{esc(labels[s])}</b><span>{esc(stars_text(ratings[s]))}</span></div>')
    return (f'<div class="op-radar-box">{svg_image(svg, "op-radar-img", "Hexagone de tes compétences")}'
            f'{"".join(labels_html)}</div>')


def skill_list(rows: list[dict]) -> str:
    """rows : {label, rating, change (★ sur 7 jours ou None), tag (texte ou None), accent (bool)}."""
    out = []
    for r in rows:
        tag = (f'<span class="op-skill-tag{" op-tag-accent" if r.get("accent") else ""}">{esc(r["tag"])}</span>'
               if r.get("tag") else "")
        change = r.get("change")
        if change is not None and abs(change) >= 0.01:
            delta = f'<span class="{"op-good" if change > 0 else "op-bad"}">{f"{change:+.2f}".replace(".", ",")}</span>'
        else:
            delta = "–"
        out.append(f'<div class="op-skill"><div><span class="op-skill-name">{esc(r["label"])}</span>{tag}</div>'
                   f'<div class="op-skill-value">{esc(stars_text(r["rating"]))}</div>'
                   f'<div class="op-skill-delta">{delta}</div></div>')
    return f'<div class="op-skills">{"".join(out)}</div>'


# --- Paliers et succès -------------------------------------------------------------------------------

def tier_tracks(rows: list[dict]) -> str:
    """rows : {label, current_c, step_c, met, needed}. Une barre par compétence, toutes sur la même échelle
    d'étoiles : un segment par palier (validés en gris, en cours en accent, à venir vides). Une compétence en
    retard se voit donc d'un coup d'œil."""
    if not rows:
        return ""
    step = rows[0]["step_c"]
    lo = max(step, min(r["current_c"] for r in rows) - 4 * step)
    hi = max(r["current_c"] for r in rows) + 2 * step
    levels = range(lo, hi + 1, step)
    out = []
    for r in rows:
        segs = "".join(
            f'<div class="op-step{" op-step-done" if lv < r["current_c"] else " op-step-current" if lv == r["current_c"] else ""}"'
            f' title="{esc(stars_text(lv / 100))}"></div>' for lv in levels)
        out.append(f'<div class="op-track"><div class="op-track-name">{esc(r["label"])}</div>'
                   f'<div class="op-track-steps">{segs}</div>'
                   f'<div class="op-track-level">{esc(stars_text(r["current_c"] / 100))}'
                   f'<small>{r["met"]}/{r["needed"]} maps</small></div></div>')
    return f'<div class="op-tracks">{"".join(out)}</div>'


def badges(items: list[dict]) -> str:
    """items : {symbol, title, text, unlocked (date ou None), progress}."""
    out = []
    for a in items:
        foot = progress(a["progress"]) if not a["unlocked"] and a["progress"] > 0 else ""
        sub = esc(a["unlocked"]) if a["unlocked"] else esc(a["text"])
        out.append(f'<div class="op-badge{"" if a["unlocked"] else " op-badge-off"}">{icon(a["symbol"])}'
                   f'<div class="op-badge-title">{esc(a["title"])}</div><div class="op-badge-text">{sub}</div>'
                   f'{foot}</div>')
    return f'<div class="op-badges">{"".join(out)}</div>'
