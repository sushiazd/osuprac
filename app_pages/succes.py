"""Page « Succès » : série de jours, bilan de la semaine et succès débloqués."""
from datetime import date

import streamlit as st

from osuprog import db, rewards, visuals
from osuprog.skills import LABELS
from osuprog.ui import pct, stars

conn = db.connect()
today = date.today().isoformat()
streak = rewards.streaks(rewards.play_days(conn), today)
week = rewards.week_summary(conn, today)
achievements = rewards.achievements(conn, today)
unlocked = sorted((a for a in achievements if a.unlocked), key=lambda a: a.unlocked, reverse=True)
next_up = sorted((a for a in achievements if not a.unlocked), key=lambda a: -a.progress)


def symbol(a) -> str:
    return a.icon.removeprefix(":material/").rstrip(":")


def badge(a) -> str:
    when = date.fromisoformat(a.unlocked).strftime("%d/%m/%Y") if a.unlocked else None
    return visuals.achievement_badge(a.title, a.text, symbol(a), a.level, when, a.progress)


st.html(visuals.page_header("emoji_events", "Succès",
                            f"{len(unlocked)} débloqués · ta régularité, tes records et les étapes franchies",
                            "#ffd23f"))
st.html(visuals.streak_hero(streak["current"], streak["best"], streak["played_today"]))

st.subheader("Ta semaine", icon=":material/calendar_month:")
minutes = week["minutes"]
st.html(visuals.stat_tiles([
    ("sports_esports", str(week["plays"]), f"parties · {week['passed']} finies", "#ff66aa"),
    ("schedule", f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min", "temps de jeu", "#66ccff"),
    ("calendar_today", f"{week['days_played']}/7", "jours joués", "#ffaa33"),
    ("flag", str(week["tiers"]), "paliers validés", "#aa88ff"),
    ("star", stars(week["hardest"]) if week["hardest"] is not None else "–", "map la plus dure finie", "#ffd23f"),
    ("adjust", pct(week["best_acc"]) if week["best_acc"] is not None else "–",
     f"meilleure précision ≥ {stars(week['level'])}" if week.get("level") else "meilleure précision", "#5cf08f"),
]))
changes = {c: v for c, v in week["rating_change"].items() if abs(v) >= 0.01}
if changes:
    st.html('<div class="op-focus-row" style="margin-top:12px">' + "".join(
        f'<div class="op-focus" style="--c:{visuals.SKILL_COLORS[c]}"><div class="op-focus-top">'
        f'{visuals.icon(visuals.SKILL_SYMBOLS[c])}<b>{visuals.esc(LABELS[c])}</b>'
        f'<span class="op-focus-share">{f"{v:+.2f}".replace(".", ",")}★</span></div>'
        '<div class="op-focus-note">sur 7 jours</div></div>'
        for c, v in sorted(changes.items(), key=lambda x: -x[1])) + "</div>")

st.subheader("Prochains succès", icon=":material/lock_open:")
st.html(visuals.badge_grid([badge(a) for a in next_up]))

st.subheader(f"Débloqués ({len(unlocked)})", icon=":material/workspace_premium:")
st.html(visuals.badge_grid([badge(a) for a in unlocked]))
