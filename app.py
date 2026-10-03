"""Tableau de bord osu! progression : streamlit run app.py (ou double-clic sur lancer.bat)."""
import subprocess

import streamlit as st

from osuprog import db
from osuprog.api import AuthError, MissingCredentials
from osuprog.config import OSU_USERNAME
from osuprog.rating import load as load_ratings
from osuprog.sync import sync
from osuprog.ui import since

st.set_page_config(page_title="osu! progression", page_icon=":material/sports_esports:", layout="wide")


@st.cache_data(ttl=300)
def auto_sync_enabled() -> bool:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", "osu-progression sync"], capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def run_sync(username: str) -> None:
    with st.status("Synchronisation…", expanded=True) as status:
        try:
            result = sync(username, log=status.write)
        except MissingCredentials as e:
            status.update(label="Clé API manquante", state="error")
            st.error(str(e))
            return
        except AuthError as e:
            status.update(label="Clé API refusée", state="error")
            st.error(str(e))
            return
        except ValueError as e:
            status.update(label="Pseudo manquant ou introuvable", state="error")
            st.error(str(e))
            return
        except Exception as e:
            status.update(label="Échec de la synchronisation", state="error")
            st.exception(e)
            return
        new = result["new_scores"]
        status.update(label=f"À jour : {new} nouvelle{'s' if new > 1 else ''} partie{'s' if new > 1 else ''}",
                      state="complete", expanded=False)
    st.rerun()


conn = db.connect()
user = db.get_meta(conn, "user")
has_data = bool(load_ratings(conn))

with st.sidebar:
    if user:
        with st.container(horizontal=True, vertical_alignment="center"):
            if user.get("avatar_url"):
                st.image(user["avatar_url"], width=56)
            with st.container(gap=None):
                st.markdown(f"**{user['username']}**")
                rank = f"#{user['global_rank']:,}".replace(",", " ") if user.get("global_rank") else "non classé"
                st.caption(f"{user['pp'] or 0:.0f} pp · {rank}")
    username = st.text_input("Pseudo osu!", value=(user or {}).get("username") or OSU_USERNAME)
    if st.button("Synchroniser", type="primary", icon=":material/sync:", width="stretch"):
        run_sync(username)
    last = db.get_meta(conn, "last_sync")
    if last:
        st.caption(f"Dernière synchro : {since(last)}")
    if auto_sync_enabled():
        st.caption(":material/schedule: Synchro automatique : toutes les heures")
    else:
        st.caption(":material/schedule: Synchro automatique désactivée : double-clique sur "
                   "`activer-synchro-auto.bat` pour ne perdre aucune partie.")

if has_data:
    pages = [
        st.Page("app_pages/aujourdhui.py", title="Aujourd'hui", icon=":material/today:", default=True),
        st.Page("app_pages/competences.py", title="Mes compétences", icon=":material/insights:"),
        st.Page("app_pages/paliers.py", title="Progression", icon=":material/stairs:"),
        st.Page("app_pages/historique.py", title="Historique", icon=":material/history:"),
        st.Page("app_pages/guide.py", title="Guide", icon=":material/help:"),
    ]
else:
    pages = [st.Page("app_pages/bienvenue.py", title="Bienvenue", icon=":material/waving_hand:", default=True)]

st.navigation(pages, position="top").run()
