"""Page de premier lancement : comment connecter l'outil à ton compte osu!."""
import streamlit as st

st.title("Bienvenue")
st.caption("Chaque jour, une liste de maps à jouer dans l'ordre pour progresser là où tu en as besoin.")

with st.container(border=True):
    st.markdown("**Connecte ton compte (une seule fois)**")
    st.markdown(
        "1. Sur [osu.ppy.sh/home/account/edit](https://osu.ppy.sh/home/account/edit#oauth), section **OAuth**, "
        "clique sur **Nouvelle application OAuth**. Nom : ce que tu veux. URL de rappel : `http://localhost`.\n"
        "2. Copie l'**ID client** et le **secret client** dans le fichier `.env` du dossier de l'outil.\n"
        "3. Écris ton pseudo à gauche et clique sur **Synchroniser**."
    )
st.caption("Ta clé API reste sur ton PC. Ne l'envoie à personne.")
