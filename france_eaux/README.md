# Les eaux de France

Pendant français de `europe_eaux/` : rivières selon leur débit, lignes de partage des eaux, relief
en trame, plus les forêts de `france_forets/` et des teintes de terre selon le versant.

Format 800 × 515 mm (cadre 750 × 465 mm), ≈ 1:2 360 000. Point de vue de Google Maps en
« vue globe » : projection perspective verticale depuis **1 834 km** d'altitude au-dessus de
46,49° N, 3,42° E, nord en haut, avec le même cadrage que la capture (de Londres et Leipzig à
Valladolid et Rome). Ces valeurs ont été retrouvées par moindres carrés sur la position de 36 villes
de la capture (écart moyen 1,2 pixel). L'horizon est hors du cadre : la rondeur de la Terre se lit à
la courbure du canevas et au léger resserrement des bords (−3 % à 500 km du centre, −12 % à 1 000 km).

## Calques (ordre d'impression)

| # | Fichier | Encre | Contenu |
|---|---------|-------|---------|
| 1 | `calque_1_jaune` | jaune paille | terres des versants Manche, mer Noire et Méditerranée, hors forêts |
| 2 | `calque_2_rose` | rose saumon | terres des versants Atlantique, mer du Nord et Méditerranée, hors forêts |
| 3 | `calque_3_foret` | vert | forêts (couvert arboré ≥ 50 % alentour, ESA WorldCover 2021) |
| 4 | `calque_4_relief` | marron foncé | estompage du relief (ETOPO 2022 à 30″) en trame de points, 18 lignes/cm |
| 5 | `calque_5_eaux` | bleu | mers et lacs ; rivières de plus de 3 m³/s, de 0,2 à 1,1 mm selon le débit ; noms des rivières en italique ; noms des mers en réserve (papier) |
| 6 | `calque_6_noir` | noir | lignes de partage des eaux entre versants (tirets 0,7 mm) et entre grands bassins (pointillé 0,45 mm), frontières (0,3 mm), canevas tous les 2° (0,2 mm), légende |

Le jaune et le rose se superposent sur la Méditerranée (orangé) : trois teintes avec deux encres.
La neige au-dessus de 2 800 m est en réserve dans les deux. La légende est dans un cartouche en réserve
(papier) au large de la Bretagne. `france_eaux_apercu.svg/pdf` : simulation en couleur (un calque
Inkscape par encre, la superposition jaune + rose simulée par multiplication des couleurs).

## Notes pour l'atelier

- 800 × 515 mm, 4 croix de repérage, nom du calque en marge basse ; films en PDF (noir 100 % sur
  blanc, texte vectorisé), SVG modifiables à côté.
- Ordre : 1 jaune (aperçu `#ecd68e`), 2 rose (`#efb8a0`), 3 vert (`#7f9f5a`), 4 marron (`#6b4a2b`),
  5 bleu (`#3d6f9e`), 6 noir.
- **Le jaune et le rose doivent être transparents** (base transparente) : leur superposition fait
  l'orangé de la Méditerranée. À essayer sur une chute avant le tirage.
- Débords : jaune et rose passent 0,3 mm sous le vert et sous le bleu, le vert sous le bleu ; entre
  le jaune et le rose, chacun déborde de 0,15 mm sur l'autre (sous les tirets noirs). La trame, les
  rivières, leurs noms et tout le noir s'impriment par-dessus, sans réserve.
- Trame du relief : plus petit point ~0,22 mm → écran à maille fine (120 à 150 fils/cm).

## Méthode

- **Teintes des versants** : les sous-bassins sont rastérisés à 0,2 mm sur la carte avec leur versant,
  puis chaque encre de terre reçoit sa zone (`TEINTES`), lissée comme les autres aplats. Trois versants
  se touchent deux à deux (Manche, Atlantique, Méditerranée) : trois teintes sont nécessaires ; la mer
  du Nord reprend celle de l'Atlantique et la mer Noire celle de la Manche, qu'elles ne touchent pas.
- **Versants** : comme pour `europe_eaux/`, chaque fleuve est rattaché à la mer de l'OHI où il se jette,
  mais avec les versants à la française : Manche, Atlantique, mer du Nord, Méditerranée (et mer Noire
  pour le Danube). Les bassins viennent de HydroBASINS **niveau 8** : le niveau 5 de la carte d'Europe
  regroupe des fleuves côtiers (la Somme avec l'Escaut, l'Orne avec la Vilaine), ce qui fausse les
  versants à cette échelle. Grands bassins (pointillé) : plus de 8 000 km² (Seine, Loire, Garonne,
  Rhône, Rhin, Meuse, Escaut, Adour, Charente, Vilaine…).
- **Noms des rivières** : liste `RIVIERES` (nom et un point sur le cours). Le nom suit le tracé
  HydroRIVERS imprimé (cours principal, vers l'aval et vers l'amont), au plus près du point, sur un
  arc régulier ; il s'écarte de la rivière si elle méandre, ne touche ni l'eau épaisse ni sa propre
  rivière ni un autre nom, et les rivières fines s'interrompent dessous. Corps selon le débit.
- **Forêts** : densité locale de couvert arboré (WorldCover, lue à ~320 m) calculée sur la grille de la
  carte à 0,2 mm, lissée sur 0,5 mm, seuil 50 %, taches et trous < 0,8 mm² supprimés, contours lissés.
- **Côtes** : la France d'après les départements IGN, les voisins d'après Natural Earth 10m ; lacs
  Natural Earth (dont la couche Europe) et Léman / lac de Constance d'OpenStreetMap.
- **Estuaires** : les limites des départements passent au milieu de l'eau, si bien que la Gironde, la
  basse Seine, la basse Loire et la baie de Somme y sont de la terre, alors que HydroRIVERS s'arrête au
  fond de l'estuaire (bec d'Ambès, Tancarville). Dans des cadres limités à l'intérieur de chaque estuaire
  (`ESTUAIRES`, en lon/lat), l'eau de Natural Earth l'emporte ; ailleurs la côte reste celle de l'IGN.

## Régénérer

```sh
.venv/bin/python france_eaux/01_telecharger_france_eaux.py   # ~6 min la première fois
.venv/bin/python france_eaux/02_carte.py                     # ~2 min
```

Le script de téléchargement lance au besoin ceux de `europe/`, `europe_eaux/` et `france_forets/`,
dont les données sont partagées. `02_carte.py` réutilise les fonctions de `europe/02_carte.py` et de
`europe_eaux/02_carte.py`, en remplaçant leurs réglages de vue et de format par les siens.

Réglages en tête de `02_carte.py` :
- vue : `LAT_0`, `LON_0`, `ALTITUDE_KM` (plus bas = Terre plus ronde), `CENTRE_KM` (centre du cadre
  dans la vue), `LARGEUR_KM` (largeur couverte), `PAGE_L`, `PAGE_H` ;
- versants : `TEINTES` (encres de terre de chaque versant), `LEGENDE_VERSANTS` ;
- rivières : `DEBIT_MIN`, `FACTEUR_RIVIERE`, `TRAIT_RIVIERE_MM` ;
- forêts : `SEUIL_DENSITE`, `LISSAGE_FORET_MM`, `AIRE_MIN_FORET_MM2` ;
- relief : `PIXEL_RELIEF_M`, `LISSAGE_RELIEF_M`, `EXAGERATION` (les autres réglages de trame et la
  neige sont ceux de `europe/`) ;
- partage des eaux : `VERSANTS`, `BASSIN_MIN_KM2`, traits et tirets ;
- estuaires : `ESTUAIRES` (cadres lon/lat où l'eau de Natural Earth l'emporte sur les départements) ;
- noms : `MERS` (lon, lat, corps, angle éventuel), `RIVIERES`, `RIVIERE_MM` et `ECART_NOM_MM` (selon le
  débit), `ECART_ARC_MM`, `TRAIT_COUPE_MM` ; légende : `CARTOUCHE` (position en mm).

## Sources

- Rivières : HydroRIVERS v1.0 (régions eu et af) ; bassins : HydroBASINS v1c niveau 8 (région eu) ;
  mers : IHO Sea Areas v3 (Marine Regions). Voir `europe_eaux/README.md`.
- Forêts : ESA WorldCover 2021 v200 (classe « arbres »), aperçus à ~320 m.
- Relief : ETOPO 2022 v1 à 30″ (NOAA NCEI), découpé de 25° O à 28° E et de 27° N à 63° N.
- Point de vue : capture d'écran de Google Maps en vue globe (8 octobre 2026), qui sert seulement
  à caler la perspective (`data/`, non versionnée).
- Départements : gregoiredavid/france-geojson ; lacs : Natural Earth 10m et OpenStreetMap
  (voir `france_forets/README.md`) ; terres, pays, frontières : Natural Earth 10m v5.1.1.
