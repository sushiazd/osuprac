"""Page de premier lancement : comment connecter l'outil à ton compte osu!."""
import streamlit as st

from osuprog import visuals

st.html(visuals.page_header("waving_hand", "Bienvenue", "Ta progression osu!, compétence par compétence", "#ff66aa"))
st.markdown(
    "Cet outil analyse tes parties osu!, trouve tes points faibles et te donne chaque jour une liste de maps "
    "à jouer, dans l'ordre, pour progresser le plus vite possible."
)

with st.container(border=True):
    st.markdown("#### :material/key: Connecte ton compte (une seule fois)")
    st.markdown(
        "1. Sur [osu.ppy.sh/home/account/edit](https://osu.ppy.sh/home/account/edit#oauth), section **OAuth**, "
        "clique sur **Nouvelle application OAuth**. Nom : ce que tu veux. URL de rappel : `http://localhost`.\n"
        "2. Copie l'**ID client** et le **secret client** dans le fichier `.env` du dossier de l'outil.\n"
        "3. Écris ton pseudo à gauche et clique sur **Synchroniser**. Ça prend quelques secondes."
    )
st.caption("Ta clé API reste sur ton PC, dans le fichier .env. Ne l'envoie à personne.")
