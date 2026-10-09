# Plan d'implémentation : Clarté des paliers et remplacement des maps

**Objectif :** Expliciter les critères de validation des maps de palier avec leurs objectifs précis et records personnels, et permettre le remplacement immédiat d'une map jugée trop difficile.  
**Architecture :** Ajout de la fonction métier `replace_tier_map` dans `osuprog/progression.py` pour écarter et réapprovisionner instantanément les maps de palier ; enrichissement des cartes de maps dans `osuprog/ui.py` et de la page `app_pages/paliers.py` avec encadré explicatif des critères et boutons d'action de remplacement.  
**Stack Technique :** Python 3.12, SQLite, Streamlit, Pytest.  
---

### Tâche 1 : Logique métier de remplacement d'une map de palier (`replace_tier_map`)

**Fichiers concernés :**
- À modifier : `osuprog/progression.py`
- Test à lier : `tests/test_data.py`

**Étape 1 : Écriture du test ciblé (Test-Driven Development)**
Ajouter un test dans `tests/test_data.py` vérifiant le comportement de `replace_tier_map` :
```python
def test_replace_tier_map(conn):
    from osuprog.skills import CATEGORIES
    cat = CATEGORIES[0]
    level_c = 300
    conn.execute("INSERT OR REPLACE INTO progress VALUES (?, ?)", (cat, 275))
    maps = progression.ensure_tier_maps(conn, None, cat, level_c)
    assert len(maps) == 3
    victim = maps[0]

    new_id = progression.replace_tier_map(conn, None, victim, cat, level_c)

    assert new_id is not None
    assert new_id != victim
    assert victim in progression.ignored_maps(conn)
    current_maps = [r["beatmap_id"] for r in conn.execute(
        "SELECT beatmap_id FROM tier_maps WHERE category = ? AND level_c = ?", (cat, level_c)
    ).fetchall()]
    assert len(current_maps) == 3
    assert victim not in current_maps
    assert new_id in current_maps
```

**Étape 2 : Vérification de l'échec du test**
Lancer la console : `.\.venv\Scripts\pytest tests/test_data.py -k test_replace_tier_map`
Résultat attendu : ÉCHEC (`AttributeError: module 'osuprog.progression' has no attribute 'replace_tier_map'`)

**Étape 3 : Implémentation du code minimaliste pour la validation**
Ajouter dans `osuprog/progression.py` :
```python
def replace_tier_map(conn, api, beatmap_id: int, category: str, level_c: int) -> int | None:
    """Écarte une map de palier jugée trop dure et la remplace immédiatement par une autre map éligible."""
    ignore_map(conn, beatmap_id)
    # Reconstitue immédiatement le palier pour combler le slot libéré
    new_maps = ensure_tier_maps(conn, api, category, level_c)
    for bid in new_maps:
        if bid != beatmap_id:
            return bid
    return None
```

**Étape 4 : Re-Vérification de succès**
Relancer les tests : `.\.venv\Scripts\pytest tests/test_data.py -k test_replace_tier_map`
Résultat attendu : SUCCÈS (1 passed).

**Étape 5 : Commit des changements propres à cette étape**
```bash
git add osuprog/progression.py tests/test_data.py
git commit -m "feat: ajout de la fonction replace_tier_map pour renouveler une map de palier"
```

---

### Tâche 2 : Composants UI et formatage des objectifs et statuts de palier

**Fichiers concernés :**
- À modifier : `osuprog/ui.py`
- Test à lier : `tests/test_data.py`

**Étape 1 : Écriture du test ciblé (Test-Driven Development)**
Ajouter un test dans `tests/test_data.py` vérifiant les helpers d'affichage de critères de palier :
```python
def test_tier_map_status(conn):
    from osuprog.ui import tier_map_status
    # Map sans scores
    status = tier_map_status(conn, 1, "aim")
    assert status["state"] == "todo"
    assert "Objectif" in status["goal_text"]
    assert status["record_text"] == "Non tentée"
```

**Étape 2 : Vérification de l'échec du test**
Lancer la console : `.\.venv\Scripts\pytest tests/test_data.py -k test_tier_map_status`
Résultat attendu : ÉCHEC (`ImportError: cannot import name 'tier_map_status' from 'osuprog.ui'`)

**Étape 3 : Implémentation du code minimaliste pour la validation**
Ajouter dans `osuprog/ui.py` :
- `tier_map_status(conn, beatmap_id: int, cat: str) -> dict` retournant le statut détaillé :
  - `state` : `'done' | 'retry' | 'todo'`
  - `goal_text` : ex. `≥ 80 % précision · ≤ 12 miss`
  - `record_text` : ex. `Validée · 84,5 % · 4 miss` ou `À retenter · 76,0 % · 18 miss` ou `Non tentée`
  - `met` : booléen indiquant la validation.
- Adaptation ou extension de l'affichage des maps avec support du bouton de remplacement et transmission des actions utilisateur.

**Étape 4 : Re-Vérification de succès**
Relancer les tests : `.\.venv\Scripts\pytest tests/test_data.py -k test_tier_map_status`
Résultat attendu : SUCCÈS.

**Étape 5 : Commit des changements propres à cette étape**
```bash
git add osuprog/ui.py tests/test_data.py
git commit -m "feat: ajout du formateur de statut et objectifs de palier tier_map_status"
```

---

### Tâche 3 : Intégration sur la page « Progression » (`app_pages/paliers.py`)

**Fichiers concernés :**
- À modifier : `app_pages/paliers.py`

**Étape 1 : Intégration de l'encadré d'explication de critère par compétence**
Pour la compétence sélectionnée (ou pour l'examen) :
Afficher un conteneur d'information clair résumant les conditions requises pour valider les maps :
- Précision minimale requise (`crit.min_acc`).
- Miss maximum autorisés (`crit.max_miss_pct %` des notes).
- Règle d'exclusion des mods facilitants (*Easy*, *Half-Time*).

**Étape 2 : Affichage détaillé des cartes de palier avec bouton « Remplacer »**
Pour chaque map de palier :
- Afficher la ligne de la map avec son objectif précis calculé pour ses notes (`≥ 80 % précision · ≤ X miss`).
- Afficher le statut / record du joueur (`Validée`, `À retenter : record ...`, `Non tentée`).
- Pour les maps non validées, afficher un bouton de remplacement (`:material/swap_horiz:`) avec un popover de confirmation :
  - Clic -> appel de `replace_tier_map(conn, None, beatmap_id, cat, level_c)`
  - Message toast ou confirmation + `st.rerun()`.

**Étape 3 : Vérification manuelle et compatibilité visuelle**
Vérifier l'alignement responsive, l'absence d'erreurs Streamlit et le bon fonctionnement du remplacement et du rétablissement depuis l'expander « Maps retirées ».

**Étape 4 : Commit des changements propres à cette étape**
```bash
git add app_pages/paliers.py
git commit -m "feat: explication claire des critères et bouton de remplacement des maps de palier"
```

---

### Tâche 4 : Validation globale et tests de non-régression

**Fichiers concernés :**
- L'ensemble du projet

**Étape 1 : Exécution complète de la suite de tests**
Lancer la suite complète : `.\.venv\Scripts\pytest`
Résultat attendu : 100 % des tests passent sans avertissement ni régression.

**Étape 2 : Commit final**
```bash
git commit --allow-empty -m "chore: vérification complète du plan de clarté des paliers"
```
