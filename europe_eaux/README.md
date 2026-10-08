# Les eaux de l'Europe

Pendant hydrographique de la carte politique `europe/` : même vue de l'espace (projection
perspective verticale, satellite à 3 000 km au-dessus de 41,5° N, 15° E), même format
700 × 500 mm, même terre ocre et même relief en trame. Les frontières, villes et routes
cèdent la place aux mers, aux lacs, aux rivières hiérarchisées selon leur débit, et aux
lignes de partage des eaux.

## Calques (ordre d'impression)

| # | Fichier | Encre | Contenu |
|---|---------|-------|---------|
| 1 | `calque_1_terre` | ocre clair | toutes les terres ; déborde de 0,3 mm sous le bleu ; neige au-dessus de 2 800 m et glace de l'Arctique en réserve |
| 2 | `calque_2_relief` | marron foncé | estompage du relief en trame de points (identique à `europe/`) |
| 3 | `calque_3_eaux` | bleu | mers, océans et lacs ; rivières de plus de 8 m³/s, de 0,2 à 0,9 mm selon le débit ; noms des rivières en italique bleu ; noms des mers en réserve (papier) dans le bleu |
| 4 | `calque_4_noir` | noir | lignes de partage des eaux : entre versants des mers (tirets 0,6 mm) et entre grands bassins fluviaux (pointillé 0,4 mm) ; canevas tous les 10° (0,2 mm) ; légende dans le ciel en haut à gauche |

`europe_eaux_apercu.svg/pdf` : simulation en couleur (un calque Inkscape par encre).

## Notes pour l'atelier

- Format 700 × 500 mm, 4 croix de repérage, nom du calque en marge basse ; films en PDF
  (noir 100 % sur blanc, texte vectorisé), SVG modifiables à côté.
- Ordre : 1 ocre clair (aperçu `#e8d08c`), 2 marron foncé (`#6b4a2b`), 3 bleu (`#3d6f9e`), 4 noir.
- Les rivières, leurs noms, la trame et tout le noir s'impriment par-dessus l'ocre sans réserve.
- Noms des mers : lettres en réserve dans l'aplat bleu (corps ≥ 2,6 mm, capitales grasses) ;
  dans le SVG du calque 3, ce sont des textes blancs posés sur l'aplat.
- Rivières : traits de 0,2 mm au plus fin ; trame du relief : maille fine (120 à 150 fils/cm).

## Partage des eaux : méthode

Chaque bassin de niveau 5 de HydroBASINS connaît son bassin principal (le fleuve qui
l'emporte jusqu'à la mer). L'exutoire de ce bassin principal est rattaché à la mer de l'OHI
la plus proche, et la mer à son versant : Atlantique, mer du Nord, Baltique, Arctique
(dont mer de Norvège, de Barents, Blanche, de Kara), Méditerranée, mer Noire (et d'Azov),
océan Indien. Les bassins endoréiques qui se jettent dans la Caspienne forment le versant
caspien, les autres (plus de 20 000 km²) un versant intérieur ; les petits sont fondus dans
leurs voisins. Les bassins principaux de plus de 40 000 km² (Volga, Danube, Rhin, Loire,
Seine, Pô, Èbre…) sont entourés d'un pointillé.

Les bassins sont rastérisés sur la carte à 0,2 mm (les régions HydroSHEDS ne se raccordent
pas exactement), les limites suivies sur les bords des pixels puis lissées (Chaikin), et
gardées sur la terre seulement.

## Régénérer

```sh
.venv/bin/python europe/01_telecharger_europe.py        # fond commun, s'il manque
.venv/bin/python europe_eaux/01_telecharger_eaux.py     # ~330 Mo à télécharger (1,3 Go décompressés), ~3 min
.venv/bin/python europe_eaux/02_carte.py                   # ~3 min
```

Le script réutilise les fonctions et les réglages de `europe/02_carte.py` (vue, format, relief,
glace, nettoyage). Réglages propres en tête de `02_carte.py` :
- rivières : `DEBIT_MIN` (m³/s), `FACTEUR_RIVIERE` et `TRAIT_RIVIERE_MM` (épaisseur ∝ débit^¼),
  `LISSAGE_RIVIERE_MM` (les tracés HydroSHEDS sont lissés, sans marches de pixels) ;
- partage des eaux : `VERSANTS` (mers de l'OHI par versant), `BASSIN_MIN_KM2`, `ENDO_MIN_KM2`,
  traits et tirets `TRAIT_VERSANT_MM`, `TIRETS_VERSANT_MM`, `TRAIT_BASSIN_MM`, `TIRETS_BASSIN_MM` ;
- noms des mers : `MERS` (nom, lon, lat, corps, angle éventuel ; sinon le long du parallèle) ;
- noms des rivières : `RANG_NOMS`, `RIVIERE_MM` (corps selon le rang), `ECART_NOM_MM` (écart à la rivière),
  `NOMS_RIVIERES` (corrections, `None` = pas de nom). Chaque nom suit un arc régulier ajusté sur la
  rivière (`ECART_ARC_MM`, `COURBURE_MAX`), au-dessus ou au-dessous, sur la terre, sans toucher un autre
  nom ni une rivière de plus de `TRAIT_COUPE_MM` ; les rivières plus fines s'interrompent sous les noms.
  Environ 80 noms sur 135 trouvent leur place ; la mer Blanche, trop étroite, n'est pas nommée.

## Sources

- Rivières : HydroRIVERS v1.0 (Lehner & Grill 2013, HydroSHEDS / WWF), régions eu, af, si, as.
- Bassins versants : HydroBASINS v1c, niveau 5, mêmes régions.
- Mers : IHO Sea Areas v3 (Marine Regions, VLIZ), service WFS.
- Noms des rivières : Natural Earth 10m v5.1.1 (`ne_10m_rivers_lake_centerlines`, `name_fr`).
- Fond commun (terres, lacs, glaciers, banquise NSIDC, relief ETOPO 2022) : voir `europe/README.md`.
