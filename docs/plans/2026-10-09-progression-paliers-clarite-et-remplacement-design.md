# Design Document : Clarté des paliers et remplacement des maps

**Date :** 2026-10-09  
**Statut :** Validé par l'utilisateur  

## 1. Contexte & Problématique

Sur la page « Progression » (`app_pages/paliers.py`), deux irritants majeurs ont été identifiés :
1. **Incompréhension des critères de validation :** L'utilisateur ignore comment une map de palier est considérée comme « validée ». La page se contente d'afficher le nom des maps sans indiquer les seuils requis (précision minimale, nombre de miss maximum autorisés selon la longueur de la map) ni l'état de ses propres essais.
2. **Impossibilité de retirer ou changer une map jugée trop difficile :** Si une map attribuée à un palier s'avère trop ardue ou frustrante, l'utilisateur n'a aucun moyen dans l'interface de la retirer pour en recevoir une autre.

## 2. Solutions Retenues

### 2.1. Clarté des critères de validation (Issue 1)
- **Encadré d'explication de compétence :**  
  Au-dessus des maps du palier sélectionné, un encadré récapitule la règle générale de la compétence :
  - Seuil d'accuracy (ex. $\ge 80\,\%$ pour Aim).
  - Taux maximal de miss (ex. $\le 2\,\%$ des notes pour Aim).
  - Précision sur les mods interdits (mods facilitants tels que *Easy*, *Half-Time*, etc.).
- **Détails et statut par map :**  
  Pour chaque map du palier (et d'examen) :
  - Affichage de l'objectif chiffré spécifique : précision cible et nombre exact de miss autorisés calculé d'après le nombre d'objets de la map (`crit.max_miss(n_objects)`).
  - Affichage clair du statut de l'utilisateur :
    - 🟢 **Validée** : précision et miss atteints (ex. « Record : 85,2 % · 4 miss »).
    - 🟠 **À retenter** : map essayée mais seuil non atteint ou fail (ex. « Record : 77,1 % · 16 miss » ou « Échouée (fail) »).
    - ⚪ **Non tentée** : aucune tentative enregistrée.

### 2.2. Retrait et remplacement instantané d'une map (Issue 2)
- **Fonction technique `replace_tier_map(conn, api, beatmap_id, category, level_c)` :**  
  - Ajoute la map à la liste des maps écartées (`ignored_maps`).
  - La supprime de la table `tier_maps` pour le palier concerné.
  - Pioche immédiatement une map de remplacement dans le réservoir local via `ensure_tier_maps`.
  - Nettoie la séance du jour si la map y était planifiée.
- **Interface utilisateur :**  
  - Sur chaque map non encore validée, affichage d'un bouton d'action dédié « Remplacer » (`:material/swap_horiz:`) avec un popover de confirmation : « Trop difficile ? Remplacer cette map par une autre ».
  - Après confirmation, le remplacement s'effectue et l'application se recharge (`st.rerun()`) avec la nouvelle map immédiatement disponible.
  - La map écartée reste consultable dans le volet rétractable « Maps retirées » en bas de page avec la possibilité de la réintégrer via « Remettre ».

## 3. Architecture & Impact Technique

- `osuprog/progression.py` :  
  Ajout de la fonction `replace_tier_map(conn, api, beatmap_id: int, category: str, level_c: int) -> int | None`.
- `osuprog/ui.py` :  
  Enrichissement de la carte ou ligne de map pour afficher l'objectif précis et le record personnel lorsque spécifié, ou helpers dédiés pour la page des paliers.
- `app_pages/paliers.py` :  
  Intégration de l'encadré de critères par compétence, mise en valeur des objectifs et records pour chaque map, et intégration du bouton de remplacement.
- `tests/test_data.py` / `tests/test_planner.py` :  
  Tests unitaires validant `replace_tier_map` (remplacement d'une map, ajout aux maps écartées, maintien du nombre de maps de palier à 3).
