"""Tableau de bord osu! progression : streamlit run app.py (ou double-clic sur lancer.bat)."""
import subprocess
from pathlib import Path

import streamlit as st

from osuprog import db, live, rewards, visuals
from osuprog.api import AuthError, MissingCredentials
from osuprog.config import OSU_USERNAME
from osuprog.rating import load as load_ratings
from osuprog.sync import WrongPlayer, sync
from osuprog.ui import inject_theme, since

ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="osu! progression", page_icon=str(ASSETS / "icon.svg"), layout="wide")
inject_theme()
st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"))

NEWS_ICONS = {"tier": ":material/flag:", "rank": ":material/school:", "achievement": ":material/emoji_events:"}


@st.cache_data(ttl=300)
def auto_sync_enabled() -> bool:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", "osu-progression sync"], capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def run_sync(username: str | None) -> None:
    with st.status("Synchronisation…", expanded=True) as status:
        try:
            result = sync(username, log=status.write)
        except db.SyncBusy:
            status.update(label="Synchronisation déjà en cours", state="running", expanded=False)
            st.info("Une synchronisation tourne déjà : réessaie dans quelques secondes.")
            return
        except MissingCredentials as e:
            status.update(label="Clé API manquante", state="error")
            st.error(str(e))
            return
        except AuthError as e:
            status.update(label="Clé API refusée", state="error")
            st.error(str(e))
            return
        except WrongPlayer as e:
            status.update(label="Autre joueur", state="error")
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


def quiet_sync() -> bool:
    """Synchro du suivi en direct : sans journal à l'écran ; une erreur est simplement retentée plus tard."""
    try:
        sync(log=lambda *_: None)
    except Exception:
        return False
    st.rerun(scope="app")


conn = db.connect()
user = db.get_meta(conn, "user")
has_data = bool(load_ratings(conn))

for event in rewards.unseen_news(conn):
    st.toast(f"**{event['title']}** · {event['text']}", icon=NEWS_ICONS.get(event["kind"]), duration="long")
rewards.mark_news_seen(conn)

with st.sidebar:
    if user:
        st.html(visuals.user_card(user))
        if not visuals.has_photo(user):
            st.caption("Pas de photo de profil sur osu! : ajoute-la sur ton profil, elle apparaîtra ici.")
        username = None  # la base suit ce joueur : on le retrouve par son identifiant
    else:
        username = st.text_input("Pseudo osu!", value=OSU_USERNAME)
    if st.button("Synchroniser", icon=":material/sync:", width="stretch"):
        run_sync(username)
    last = db.get_meta(conn, "last_sync")

    if live.realm_mtime() is not None:
        live_on = st.toggle("Suivi en direct", value=db.get_meta(conn, "live_enabled", True),
                            help="Synchronise tout seul à la fin de chaque partie dans lazer, tant que cette page "
                                 "est ouverte.")
        if live_on != db.get_meta(conn, "live_enabled", True):
            db.set_meta(conn, "live_enabled", live_on)
            conn.commit()

        if live_on and has_data:
            @st.fragment(run_every="10s")
            def live_watch() -> None:
                c = db.connect()
                if live.pending(c):
                    with st.spinner("Nouvelle partie…"):
                        live.check(c, quiet_sync)
                c.close()

            live_watch()
    if last:
        st.caption(f"Dernière synchro {since(last)}")
    if not auto_sync_enabled():
        st.caption("Synchro automatique désactivée : lance `activer-synchro-auto.bat` pour ne perdre aucune partie.")

if has_data:
    pages = [
        st.Page("app_pages/aujourdhui.py", title="Aujourd'hui", icon=":material/today:", default=True),
        st.Page("app_pages/competences.py", title="Compétences", icon=":material/insights:"),
        st.Page("app_pages/paliers.py", title="Progression", icon=":material/flag:"),
        st.Page("app_pages/historique.py", title="Historique", icon=":material/history:"),
        st.Page("app_pages/guide.py", title="Guide", icon=":material/help:"),
    ]
else:
    pages = [st.Page("app_pages/bienvenue.py", title="Bienvenue", icon=":material/waving_hand:", default=True)]

st.navigation(pages, position="top").run()
