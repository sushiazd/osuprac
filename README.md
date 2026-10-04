# osu! progression

Un outil perso qui mesure ton niveau dans chaque compétence d'osu! (lazer), repère tes points faibles et te propose des paliers à valider, du plus facile au plus dur.

## Installation (une seule fois)

1. Installe [Python 3.12](https://www.python.org/downloads/) en cochant **Add python.exe to PATH**, puis décompresse le dossier dans un endroit au chemin court (Documents, Bureau ou `C:\osu-progression`). Windows limite la longueur des chemins de fichiers, et certaines dépendances sont rangées très profond.
2. Crée ta clé API : [osu.ppy.sh/home/account/edit](https://osu.ppy.sh/home/account/edit#oauth), section **OAuth**, puis **Nouvelle application OAuth**. Mets le nom que tu veux et `http://localhost` comme URL de rappel (elle ne sert pas). Chaque joueur doit créer la sienne.
3. Double-clique sur `lancer.bat`. La première fois, il installe les dépendances (1 à 2 minutes) et ouvre le fichier `.env` : remplis `OSU_CLIENT_ID`, `OSU_CLIENT_SECRET` et `OSU_USERNAME`, enregistre, puis relance `lancer.bat`.

Sans le `.bat`, ouvre un terminal **dans le dossier qui contient `app.py`** et lance, dans l'ordre :

```bash
python -m venv .venv
```

```bash
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Le dossier `.venv` n'est pas fourni : il est créé par `lancer.bat` ou par la première commande ci-dessus. Si PowerShell affiche « Le module ".venv" n'a pas pu être chargé », c'est que ce dossier n'existe pas encore, ou que le terminal n'est pas ouvert au bon endroit.

## Utilisation

Double-clique sur `lancer.bat`, ou lance :

```bash
.\.venv\Scripts\streamlit run app.py
```

Le tableau de bord s'ouvre dans le navigateur, avec 6 onglets :

- **Aujourd'hui** : les maps à jouer dans l'ordre. Chacune indique pourquoi la jouer et l'objectif à atteindre. « Ouvrir dans osu! » affiche la map dans lazer (qui la télécharge), « Ne plus me proposer » l'écarte pour de bon.
- **Mes compétences** : ta note dans chaque compétence avec sa marge d'erreur, son évolution, et ta précision au clic tirée de tes replays.
- **Progression** : ton niveau validé, ce qu'il manque pour le suivant, et les paliers de chaque compétence.
- **Succès** : ta série de jours joués, le bilan de la semaine et les succès débloqués.
- **Historique** : toutes les parties analysées et leur effet sur tes notes.
- **Guide** : ce que veulent dire tous les chiffres.

L'interface reprend l'esprit d'osu! lazer : thème sombre, couleurs officielles des étoiles, bannières des maps, hexagone des compétences, passe de paliers et badges de succès. Couleurs et polices dans `.streamlit/config.toml`, effets dans `osuprog/theme.css` (aucun caractère « < » dans ce fichier : le filtre HTML de Streamlit supprimerait toute la feuille), composants dans `osuprog/visuals.py`. Les animations se coupent si Windows demande de réduire les animations. Avec le **suivi en direct** (activé par défaut dans la barre de gauche), le tableau de bord se synchronise tout seul à la fin de chaque partie dans lazer : la map se coche et la suivante s'affiche. Sinon, clique sur **Synchroniser** après ta session. L'API ne garde tes parties récentes (fails compris) que 24 h : synchronise au moins une fois par jour.

La synchronisation marche aussi sans l'interface :

```bash
.\.venv\Scripts\python -m osuprog sync
```

## Comment ça marche

**0. Ce que l'outil récupère.** Deux sources, combinées sans doublon :

- **En ligne, depuis ton inscription :** ton meilleur score sur chaque map, le nombre de fois où tu as joué chaque map, et tes parties des dernières 24 h (fails compris). Une map classée jouée au moins 3 fois sans aucun score enregistré compte comme jamais passée.
- **Dans osu! lazer, sur ce PC :** toutes les parties terminées depuis l'installation de lazer, y compris tes passes moins bonnes, les maps non classées et les parties jouées sans être connecté (« Guest »). Nécessite [Node.js](https://nodejs.org) ; le fichier de lazer est lu sur une copie, jamais modifié.

Ce qui reste hors d'atteinte : les fails de plus de 24 h qu'osu! n'a jamais gardés, et les parties quittées en cours (jamais envoyées). Pour ne plus perdre aucun fail à partir de maintenant, double-clique une fois sur `activer-synchro-auto.bat` : une tâche Windows synchronise alors toutes les heures, sans ouvrir de fenêtre (journal dans `%LOCALAPPDATA%\osu-progression\sync.log`, notification Windows quand tu valides un palier ou débloques un succès). Pour la retirer : `schtasks /Delete /TN "osu-progression sync" /F`.

La première synchronisation prend quelques secondes (un peu plus si tu as joué des milliers de maps). Les suivantes ne relisent que les maps rejouées depuis la dernière fois. Un catalogue de ~2 900 maps déjà analysées (`osuprog/pool.json.gz`) est fourni pour choisir les maps de palier sans rien télécharger.

**1. Chaque map est classée par compétence.** `rosu-pp-py` calcule en local la difficulté de la map avec tes mods (étoiles, aim, speed, AR, OD, part des sliders). Le script repère aussi les streams directement dans le fichier `.osu` : leur longueur, leur BPM et leur part dans la map. Une map reçoit ainsi un poids dans chaque compétence. Tout est comparé à ce qui est habituel au même niveau d'étoiles : à 2★, un AR 7 est très haut, alors qu'à 6★ c'est un AR bas.

| Compétence | Signal |
|---|---|
| Aim | plus d'aim que de speed par rapport aux maps du même niveau, peu de streams |
| Speed | beaucoup de bursts et de streams, ou plus de speed que d'habitude |
| Stamina | longs streams (12+ notes) dans une map longue, ou map de 3 minutes et plus |
| Accuracy | OD plus élevé que d'habitude à ce niveau |
| Reading | AR nettement plus bas ou plus haut que d'habitude, ou HD |
| Tech | sliders qui pèsent plus lourd que d'habitude dans la difficulté |

**2. Une note par compétence, calculée comme un classement Elo.** Chaque partie compte comme un match entre toi et la map. Si tu atteins le critère de la compétence, ta note monte ; sinon, elle baisse. Plus la map était dure, plus la réussite rapporte. Ta note correspond au niveau d'étoiles où tu atteins l'objectif environ une fois sur deux. Les objectifs sont réalistes (de 75 à 85 % de précision selon la compétence, avec au plus 1,5 à 2,5 % des notes en miss) : à 3-4★, la précision médiane d'un joueur tourne autour de 80 %. À côté, l'outil affiche aussi le niveau où tu **réussis** (sans fail) une map sur deux. Tes top plays d'avant le premier lancement servent de point de départ et comptent moitié.

**3. Les paliers.** Chaque compétence a une échelle de paliers espacés de 0,25★. Un palier contient 3 maps populaires, choisies pour cette compétence. Réussis le critère sur les 3 maps pour débloquer le palier suivant. Les parties sont vérifiées automatiquement à chaque synchronisation : un critère non atteint s'affiche en rouge, un critère réussi en vert. Si ta note dépasse le palier en cours de 0,5★ (avec au moins 8 parties), ce palier est validé d'office.

**4. Le rang et l'examen.** Ton rang avance par pas de 0,5★. Pour passer au rang suivant, il faut que toutes les compétences aient atteint ce niveau, puis réussir un examen de 2 maps polyvalentes (acc ≥ 80 % et ≤ 2 % de miss).

**5. La séance du jour.** Un planificateur (`osuprog/planner.py`) construit chaque jour une séance qui tient dans ton temps (durée de tes séances habituelles, réglable) pour te faire progresser le plus vite possible :

- **Bonne difficulté :** des maps que tu finis environ 75 % du temps, avec 50 % de chances d'atteindre l'objectif de précision (réglable : intensité confort, équilibrée ou intense). Les chances sont calculées sur tes parties et recalibrées à chaque synchronisation.
- **Là où ça rapporte :** chaque compétence reçoit une part du programme selon son retard sur tes autres compétences, sa présence dans les maps que tu voudras jouer ensuite et l'incertitude de sa note. Aucune ne dépasse 40 %.
- **Jauge de vie (HP) :** les étoiles l'ignorent. Sur tes parties, l'outil mesure combien d'étoiles « vaut » un point de HP pour finir une map (et ≈ 0,15★ pour l'objectif de précision) : il évite les maps que tu finis presque jamais et remplace une map de palier bloquante (jamais finie en 3 essais si c'est très improbable, ou 8 essais sans l'objectif).
- **Variété et espacement :** chaque map chargée sur une compétence vaut moins pour la suivante ; les compétences très sollicitées ces 48 dernières heures sont ménagées.
- **Répétition ciblée :** une map ratée récemment revient (jusqu'à 8 essais), une map déjà réussie est écartée quelques jours.
- **Structure :** échauffement facile, travail ciblé (du plus accessible au plus dur), défi juste au-dessus de ton niveau de réussite, révision d'une compétence peu travaillée.

Chaque map indique pourquoi elle est là, tes chances d'atteindre l'objectif et sa durée estimée. Après chaque synchronisation, le reste de la séance est recalculé avec tes nouvelles notes (les maps déjà jouées restent en tête).

`python tools/simulate_training.py` compare cette méthode à d'autres (maps au hasard, trop dures, trop faciles, un seul point faible en bloc, ancienne méthode) sur un apprenant simulé, sous plusieurs hypothèses d'apprentissage. Limite : on ne connaît pas la vraie courbe d'apprentissage d'un joueur ; la simulation vérifie que la méthode reste bonne sous toutes ces hypothèses, elle ne prouve pas un gain réel.

## Régler l'outil

- Critères de réussite par compétence : `CRITERIA` dans `osuprog/skills.py` (précision minimale et % de miss). Un léger ajustement peut se faire sans toucher `CRITERIA_VERSION` (les paliers en cours sont réévalués) ; incrémente-le pour un vrai changement : les paliers sont recalculés à la synchro suivante, après une sauvegarde automatique de ta base
- Seuils de classement des maps : `classify()` et `TYPICAL` dans `osuprog/skills.py`. Incrémente `CLASSIFIER_VERSION` après un changement : les maps sont alors reclassées à la synchronisation suivante, sans rien retélécharger.
- Catalogue de maps : `tools/build_pool.py` le reconstruit (clé API nécessaire, quelques minutes)
- Vitesse d'évolution des notes : `SCALE`, `K_MAX` et `K_MIN` dans `osuprog/rating.py`
- Planificateur d'entraînement (intensités, plafond de part par compétence, variété) : en tête de `osuprog/planner.py`. Incrémente `PLAN_FORMAT` après un changement
- Taille des paliers, nombre de maps, validation d'office : en tête de `osuprog/progression.py`

## Données et sauvegardes

- Les données vivent dans `%LOCALAPPDATA%\osu-progression` (base `osu.sqlite`, cache `maps`, journal `sync.log`), **hors** du dossier du projet : un dossier synchronisé comme OneDrive peut verrouiller ou dupliquer une base en cours d'écriture. Un ancien dossier `data/` est recopié automatiquement au premier lancement (puis laissé tel quel : tu peux le supprimer). Pour choisir un autre endroit : `OSU_DATA_DIR=...` dans `.env`.
- Une sauvegarde de la base est faite chaque jour dans `sauvegardes/` (dans le projet, donc sauvegardée par OneDrive si le projet y est) ; les 7 dernières sont gardées. Pour revenir en arrière, copie l'une d'elles à la place de `osu.sqlite`, tableau de bord fermé.
- Une base suit **un seul joueur** : une synchro sur un autre pseudo est refusée, pour ne pas mélanger les parties. Pour suivre un autre joueur, copie l'outil ailleurs avec un autre `OSU_DATA_DIR`.
- Pour repartir de zéro (paliers compris), supprime `osu.sqlite`. Les fichiers `.osu` restent en cache dans `maps`.

## Pour développer

```bash
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

```bash
.\.venv\Scripts\python -m pytest
```

Les tests utilisent une base temporaire, jamais la tienne. `python tools/eval_classifier.py` compare des façons de classer les maps sur tes propres parties (prédiction partie par partie) : à relancer avant tout changement de `classify()`. Le 2026-10-04, ajouter les sauts, angles, rythmes irréguliers et changements de vitesse des sliders n'améliorait pas la prédiction de façon significative : le classement est resté tel quel.

## Limites

- Les fichiers `.osu` sont téléchargés depuis le miroir public osu.direct (identique au serveur officiel, mais bien plus rapide), avec repli sur osu.ppy.sh.
- Sans Node.js, les parties locales de lazer sont ignorées (la synchro en ligne marche quand même). Pour l'installer : `winget install -e --id OpenJS.NodeJS.LTS`.
- Seules les parties en osu! standard sont prises en compte. Les mods fun (relax, autopilot, auto...) sont ignorés.
- Une partie avec EZ, HT ou DA compte dans ta note (avec les étoiles réduites), mais ne valide pas un palier.
- Le classement des maps est une heuristique : si une map te semble mal classée, ajuste les seuils dans `classify()`. Les écarts entre tes compétences sont souvent dans la marge d'erreur : l'onglet *Mes compétences* indique lesquels sont vraiment nets.
- L'analyse des replays (précision au clic) est une reconstitution simplifiée du jeu : vérifiée sur 280 replays (corrélation de 0,97 avec les vrais miss), fiable en tendance, pas à la note près.
