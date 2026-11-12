"""Page de premier lancement : la clé API et le pseudo se saisissent ici, sans ouvrir le fichier .env."""
import os

import requests
import streamlit as st

from osuprog.api import AuthError, OsuApi
from osuprog.config import credentials
from osuprog.keys import parse_keys, save_env

OAUTH_PAGE = "https://osu.ppy.sh/home/account/edit#oauth"

st.title("Bienvenue")
st.caption("Chaque jour, une liste de maps à jouer dans l'ordre pour progresser là où tu en as besoin.")


def check(client_id: str, secret: str, username: str) -> dict | None:
    """Profil osu! du joueur si la clé marche et que le pseudo existe ; sinon affiche l'erreur et renvoie None."""
    try:
        with st.spinner("Vérification auprès d'osu!…"):
            user = OsuApi(client_id, secret).user(username)
    except AuthError:
        st.error("osu! refuse cette clé : l'ID client et le secret client ne vont pas ensemble. Recopie-les depuis "
                 "la page OAuth d'osu! (bouton « Éditer » sur ton application).")
        return None
    except requests.RequestException:
        st.error("Impossible de joindre osu! : vérifie ta connexion internet, puis réessaie.")
        return None
    if not user:
        st.error(f"Aucun joueur « {username} » sur osu! : vérifie l'orthographe de ton pseudo.")
    return user


def key_form() -> None:
    st.markdown(
        "1. Ouvre la page OAuth d'osu! et clique sur **Nouvelle application OAuth**. Nom : ce que tu veux. "
        "URL de rappel : `http://localhost`. Enregistre."
    )
    st.link_button("Ouvrir la page OAuth d'osu!", OAUTH_PAGE, icon=":material/open_in_new:")
    with st.form("cle"):
        st.markdown("2. Copie l'**ID client** et le **secret client** (clique sur « Afficher le secret client » "
                    "pour le voir), puis colle-les ici : les deux d'un coup ou l'un sous l'autre, l'outil les "
                    "reconnaît.")
        pasted = st.text_area("ID client et secret client", height=100, label_visibility="collapsed",
                              placeholder="12345\nAbCdEf0123456789AbCdEf0123456789AbCdEf01")
        st.markdown("3. Ton pseudo osu!")
        username = st.text_input("Pseudo osu!", value=os.getenv("OSU_USERNAME", ""), label_visibility="collapsed")
        sent = st.form_submit_button("Connecter et synchroniser", type="primary", icon=":material/link:")
    if not sent:
        return
    client_id, secret = parse_keys(pasted)
    missing = [name for name, value in (("l'ID client", client_id), ("le secret client (40 caractères)", secret))
               if not value]
    if missing:
        st.error(f"Je ne trouve pas {' ni '.join(missing)} dans ce que tu as collé.")
        return
    if not username.strip():
        st.error("Écris ton pseudo osu!.")
        return
    user = check(client_id, secret, username.strip())
    if user:
        save_env({"OSU_CLIENT_ID": client_id, "OSU_CLIENT_SECRET": secret, "OSU_USERNAME": user["username"]})
        st.session_state["sync_now"] = user["username"]  # app.py lance la première synchro au prochain passage
        st.rerun()


with st.container(border=True):
    if all(credentials()):
        st.markdown("**Ta clé API est enregistrée.** Écris ton pseudo à gauche et clique sur **Synchroniser**.")
        with st.expander("Changer de clé"):
            key_form()
    else:
        st.markdown("**Connecte l'outil à osu! (une seule fois, 1 minute)**")
        key_form()
st.caption("Ta clé reste sur ton PC. Elle ne sert qu'à lire tes scores publics : ni mot de passe ni connexion à "
           "ton compte. Ne l'envoie à personne.")
