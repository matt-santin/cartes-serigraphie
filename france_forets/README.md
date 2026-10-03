# France des forêts

Carte de France, 500 × 500 mm, échelle ≈ 1:2 744 000, Lambert-93.

## Calques (ordre d'impression)

| # | Fichier | Encre | Contenu |
|---|---------|-------|---------|
| 1 | `calque_1_mer` | bleu | mer + lacs ; déborde de 0,3 mm sous toutes les côtes |
| 2 | `calque_2_terre` | terre | toutes les terres du cadre en aplat |
| 3 | `calque_3_foret` | vert foncé | zones boisées, imprimées par-dessus la terre |
| 4 | `calque_4_noir` | noir | départements, frontières, côtes et rives (0,35 mm) + cadre (1 mm) |

Le cadre est centré sur la France ; les pays voisins visibles reçoivent le même
traitement (terre + forêt), sans découpage interne. Chaque calque porte les 4 croix de repérage.
`france_forets_apercu.svg/pdf` : simulation en couleur (un calque Inkscape par encre).

## Régénérer

Prérequis : `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, et Inkscape (export PDF).

```sh
.venv/bin/python france_forets/01_telecharger_forets.py   # données, une fois (~1 min)
.venv/bin/python france_forets/02_carte.py                # ~20 s
```

Réglages en tête de `02_carte.py` : format, marge, seuil de densité (`SEUIL_DENSITE`),
lissage, taille minimale des taches, épaisseurs de trait, couleurs de l'aperçu.

## Sources

- Couverture arborée : ESA WorldCover 2021 (10 m, classe « arbres »), lue à ~160 m
  puis convertie en densité locale ; « boisé » = densité ≥ 50 % (→ 33 % du territoire).
- Départements : gregoiredavid/france-geojson (IGN Admin Express).
- Pays voisins, lacs : Natural Earth 10m (+ couche lacs Europe).
- Léman et lac de Constance : OpenStreetMap (relations 332617 et 1156846, via Nominatim).
