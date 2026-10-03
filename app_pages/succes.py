"""Page « Succès » : série de jours, bilan de la semaine et succès débloqués."""
from datetime import date

import streamlit as st

from osuprog import db, rewards
from osuprog.skills import LABELS
from osuprog.ui import pct, stars

conn = db.connect()
today = date.today().isoformat()
streak = rewards.streaks(rewards.play_days(conn), today)
week = rewards.week_summary(conn, today)
achievements = rewards.achievements(conn, today)
unlocked = [a for a in achievements if a.unlocked]
next_up = [a for a in achievements if not a.unlocked]

st.title("Succès", icon=":material/emoji_events:")
st.markdown("Ce que tu as accompli, à côté des notes : ta régularité, tes records et les étapes franchies.")

with st.container(horizontal=True):
    st.metric("Série en cours", f"{streak['current']} jour{'s' if streak['current'] > 1 else ''}", border=True,
              help="Jours de suite avec au moins une partie. Elle reste en vie tant que tu as joué hier.")
    st.metric("Meilleure série", f"{streak['best']} jour{'s' if streak['best'] > 1 else ''}", border=True)
    st.metric("Succès débloqués", f"{len(unlocked)}", border=True)
if streak["current"] and not streak["played_today"]:
    st.caption(":material/local_fire_department: Joue au moins une map aujourd'hui pour prolonger ta série.")

st.subheader("Ta semaine", icon=":material/calendar_month:")
with st.container(horizontal=True):
    st.metric("Parties", week["plays"], border=True, help=f"Dont {week['passed']} finies, sur {week['days_played']} jours.")
    st.metric("Temps de jeu", f"≈ {week['minutes'] // 60} h {week['minutes'] % 60:02d}" if week["minutes"] >= 60
              else f"≈ {week['minutes']} min", border=True)
    st.metric("Paliers validés", week["tiers"], border=True)
    if week["hardest"] is not None:
        st.metric("Map la plus dure finie", stars(week["hardest"]), border=True)
    if week["best_acc"] is not None:
        st.metric("Meilleure précision à ton niveau", pct(week["best_acc"]), border=True,
                  help=f"Sur une map d'au moins {stars(week['level'])} (ta note la plus basse).")
changes = {c: v for c, v in week["rating_change"].items() if abs(v) >= 0.01}
if changes:
    st.caption("Évolution de tes notes sur 7 jours : " + " · ".join(
        f"{LABELS[c]} {v:+.2f}★".replace(".", ",") for c, v in sorted(changes.items(), key=lambda x: -x[1])))

st.subheader("Prochains succès", icon=":material/flag:")
cols = st.columns(3)
for i, a in enumerate(next_up):
    with cols[i % 3].container(border=True, height="stretch"):
        st.markdown(f"{a.icon} **{a.title}**")
        st.caption(a.text)
        if a.progress > 0:
            st.progress(a.progress, text=f"{a.progress:.0%}")

st.subheader(f"Débloqués ({len(unlocked)})", icon=":material/workspace_premium:")
cols = st.columns(3)
for i, a in enumerate(sorted(unlocked, key=lambda a: a.unlocked, reverse=True)):
    with cols[i % 3].container(border=True, height="stretch"):
        st.markdown(f":green[{a.icon}] **{a.title}**")
        st.caption(f"{a.text} · le {date.fromisoformat(a.unlocked):%d/%m/%Y}")
