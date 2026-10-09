"""Page « pp » : tes pp par compétence, les maps pour atteindre un objectif, et le calculateur."""
import json
import math

import pandas as pd
import streamlit as st

from osuprog import db, pp, ppcalc, visuals
from osuprog.mods import mods_label
from osuprog.ui import game_link

MODS = ["HD", "HR", "DT", "NC", "HT", "EZ", "FL", "NF"]
EXCLUSIVE = [{"HR", "EZ"}, {"DT", "NC", "HT"}]

conn = db.connect()
prof = pp.profile(conn)
exact = ppcalc.engine_name() == ppcalc.LIVE


@st.cache_data(ttl=600, show_spinner=False)
def hunt(target: float, accuracy: float, pool: str, last_sync: str) -> list[dict]:
    """Mise en cache : la recherche parcourt tout le catalogue. pool/last_sync invalident le cache."""
    c = db.connect()
    found = pp.hunt(c, target, accuracy)
    c.close()
    for m in found:  # pp exacts pour 0, 1 et 3 miss (télécharge la map si besoin)
        try:
            m["by_miss"] = {k: v["pp"] for k, v in pp.map_pp(m["beatmap_id"], [], accuracy)["by_miss"].items()}
        except Exception:
            m["by_miss"] = {}
    return found


def fr(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


st.title("pp")
if prof:
    with st.container(horizontal=True):
        st.metric("Total", fr(prof["total"]), border=True,
                  help="Pondéré comme sur ton profil osu! (×0,95 à chaque rang), bonus compris.")
        for key in ("aim", "speed", "accuracy", "reading", "flashlight"):
            if key != "flashlight" or prof["skills"][key] >= 1:
                st.metric(ppcalc.SKILL_LABELS[key], fr(prof["skills"][key]), border=True)
        st.metric("Bonus", fr(prof["bonus"]), border=True,
                  help=f"Offert selon le nombre de maps classées jouées ({len(prof['scores'])}), sans lien avec ton niveau.")
else:
    st.info("Synchronise pour voir le détail de tes pp.", icon=":material/sync:")
if not exact:
    st.caption("Calcul approché (rosu-pp). Pour les valeurs exactes d'osu!, voir le Guide.")

tab_goal, tab_calc, tab_best = st.tabs(["Objectif", "Calculateur", "Meilleures parties"])

# --- Objectif : quelles maps pour faire X pp ----------------------------------------------------------
with tab_goal:
    top = prof["top"] if prof else 0
    with st.container(horizontal=True, vertical_alignment="bottom"):
        target = st.number_input("Objectif", min_value=5, max_value=1000, step=5,
                                 value=int(max(10, math.ceil((top + 1) / 10) * 10)), format="%d",
                                 help="pp d'une seule partie.")
        accuracy = st.slider("Précision visée", 80.0, 100.0, step=0.5, format="%.1f %%",
                             value=round(min(max(pp.typical_accuracy(conn) * 100, 80.0), 100.0) * 2) / 2,
                             help="Par défaut : ta précision habituelle sur tes meilleures parties.")
    with st.spinner("Recherche dans le catalogue…"):
        found = hunt(float(target), accuracy / 100, str(db.get_meta(conn, "bundled_pool")),
                     str(db.get_meta(conn, "last_sync")))
    if not found:
        st.info("Aucune map trouvée pour cet objectif à cette précision.")
    else:
        st.caption(f"Full combo à {fr(accuracy, 1)} %. Les plus accessibles d'abord : courtes, à ta portée, "
                   "faciles à finir.")
    for m in found:
        by_miss = m.get("by_miss") or {}
        fc = by_miss.get(0, m["pp"])
        sub = " · ".join(x for x in [
            f"1 miss : {fr(by_miss[1])} pp" if 1 in by_miss else "",
            f"≈ {fr(m['misses'])} miss attendus",
        ] if x)
        tip = (f"Full combo : {fr(fc, 1)} pp" + (f", 1 miss : {fr(by_miss[1], 1)}" if 1 in by_miss else "")
               + (f", 3 miss : {fr(by_miss[3], 1)}" if 3 in by_miss else "")
               + (f". Ton total gagnerait ≈ {fr(pp.gain_if(prof, fc))} pp." if prof else "."))
        with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"hunt-{m['beatmap_id']}"):
            st.html(visuals.pp_row({**m, "pp": fc}, sub, tip))
            st.link_button("", game_link(m["beatmap_id"]), type="tertiary", icon=":material/play_arrow:",
                           help="Ouvrir dans osu!")
            st.link_button("", f"https://osu.ppy.sh/beatmaps/{m['beatmap_id']}", type="tertiary",
                           icon=":material/open_in_new:", help="Page de la map")

# --- Calculateur ------------------------------------------------------------------------------------
with tab_calc:
    query = st.text_input("Map", placeholder="Lien, numéro ou titre (ex. : rammstein los hard)")
    beatmap_id = pp.parse_beatmap_id(query) if query else None
    if query and beatmap_id is None:
        results = pp.search_maps(conn, query)
        if not results:
            st.info("Aucune map du catalogue ne correspond.")
        else:
            labels = {r["beatmap_id"]: f"{r['artist']} - {r['title']} [{r['version']}]"
                      + (f" · {fr(r['stars'], 2)}★" if r["stars"] else "") for r in results}
            beatmap_id = st.selectbox("Résultats", list(labels), format_func=labels.get, label_visibility="collapsed")

    if beatmap_id:
        with st.container(horizontal=True, vertical_alignment="bottom"):
            chosen = st.pills("Mods", MODS, selection_mode="multi", default=[])
            for group in EXCLUSIVE:
                if len(group & set(chosen)) > 1:
                    st.warning(f"{' et '.join(sorted(group & set(chosen)))} ne vont pas ensemble.")
        speed = None
        if {"DT", "NC"} & set(chosen):
            speed = st.select_slider("Vitesse", [round(1.05 + 0.05 * i, 2) for i in range(20)], value=1.5)
        elif "HT" in chosen:
            speed = st.select_slider("Vitesse", [round(0.5 + 0.05 * i, 2) for i in range(10)], value=0.75)
        mods = [{"acronym": m, **({"settings": {"speed_change": speed}}
                                  if m in ("DT", "NC", "HT") and speed not in (None, 1.5, 0.75) else {})}
                for m in chosen]
        with st.container(horizontal=True):
            acc = st.number_input("Précision (%)", 0.0, 100.0, 95.0, step=0.5, format="%.2f")
            misses = st.number_input("Miss", 0, 5000, 0, step=1)
            combo = st.number_input("Combo (0 = max)", 0, 100000, 0, step=10)
        req = {"acc": acc / 100, "miss": int(misses), **({"combo": int(combo)} if combo else {})}
        try:
            with st.spinner("Calcul…"):
                res = ppcalc.calculate(beatmap_id, mods, [req] + [{"acc": a, "miss": 0} for a in (0.9, 0.95, 0.98, 1.0)])
        except Exception as e:
            st.error(f"Calcul impossible : {e}")
        else:
            s, d = res["scores"][0], res["difficulty"]
            with st.container(horizontal=True):
                st.metric("pp", fr(s["pp"], 1), border=True)
                for key in ("aim", "speed", "accuracy", "reading", "flashlight"):
                    if key != "flashlight" or "FL" in chosen:
                        st.metric(ppcalc.SKILL_LABELS[key], fr(s[key], 1), border=True)
            st.caption(f"{fr(d['stars'], 2)}★ · AR {fr(d['ar'], 1)} · OD {fr(d['od'], 1)} · CS {fr(d['cs'], 1)} · "
                       f"HP {fr(d['hp'], 1)} · combo max {d['max_combo']} · {res['engine']}")
            st.dataframe(pd.DataFrame([{"Full combo à": f"{a} %", "pp": round(x["pp"], 1)}
                                       for a, x in zip((90, 95, 98, 100), res["scores"][1:])]),
                         hide_index=True)

# --- Meilleures parties -----------------------------------------------------------------------------
with tab_best:
    if prof:
        df = pd.DataFrame([{
            "Map": f"{s['title']} [{s['version']}]", "★": s["stars"], "Mods": mods_label(json.loads(s["mods"])),
            "Précision": s["accuracy"], "Miss": s["n_miss"], "pp": s["pp"], "Aim": s["aim"], "Tap": s["speed"],
            "Acc": s["acc_pp"], "Reading": s["reading"],
            "Date": pd.to_datetime(s["ended_at"], utc=True).tz_convert(None),
        } for s in prof["scores"]])
        st.dataframe(df, hide_index=True, width="stretch", height=560, column_config={
            "Map": st.column_config.TextColumn(width="large"),
            "★": st.column_config.NumberColumn(format="%.2f"),
            "Précision": st.column_config.NumberColumn(format="%.2f %%"),
            **{c: st.column_config.NumberColumn(format="%.1f") for c in ("pp", "Aim", "Tap", "Acc", "Reading")},
            "Date": st.column_config.DatetimeColumn(format="DD/MM/YY"),
        })
