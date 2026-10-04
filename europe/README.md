# Europe vue de l'espace

Carte de l'Europe, 700 × 500 mm (paysage), en projection perspective verticale
(vue satellite) : point de vue à 3 000 km au-dessus de 41,5° N, 15° E.
L'Europe remplit la feuille, du Maroc au cap Nord, de l'Atlantique à la Caspienne ;
le haut montre l'horizon courbe du globe au-delà du pôle Nord, avec le papier blanc au-dessus.
Échelle ≈ 1:8 000 000 au centre de la vue (elle diminue vers l'horizon).

## Calques (ordre d'impression)

| # | Fichier | Encre | Contenu |
|---|---------|-------|---------|
| 1 | `calque_1_terre` | ocre clair (jaune « carte d'école ») | toutes les terres ; déborde de 0,3 mm sous la mer |
| 2 | `calque_2_mer` | bleu | mers, océans et grands lacs jusqu'à l'horizon, plus les fleuves principaux (0,35 à 0,6 mm selon leur importance), imprimés par-dessus l'ocre |
| 3 | `calque_3_noir` | noir | frontières (0,3 mm), noms des pays le long des parallèles et capitales, façon Vidal-Lablache, imprimés par-dessus le reste |

Pas de cadre : la carte s'arrête net au bord de la zone imprimable,
et le ciel au-dessus de l'horizon est le papier. Chaque calque porte les 4 croix de repérage.
`europe_apercu.svg/pdf` : simulation en couleur (un calque Inkscape par encre).

## Régénérer

Prérequis : `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, et Inkscape (export PDF).

```sh
.venv/bin/python europe/01_telecharger_europe.py   # données, une fois (~20 Mo)
.venv/bin/python europe/02_carte.py                # ~40 s
```

Réglages en tête de `02_carte.py` :
- point de vue : `LAT_0`, `LON_0` (point à la verticale) et `ALTITUDE_KM`
  (plus bas = courbure plus forte, mais nord plus écrasé) ;
- cadrage : `LARGEUR_KM` (largeur couverte au centre) et `CIEL_MM` (blanc au-dessus de l'horizon) ;
- fleuves : `RANG_MAX` (rang Natural Earth ; 7 = Danube, Rhin, Seine, Loire, Rhône, Pô, Tibre, Moselle…,
  8 ajoute Guadalquivir, Meuse, Main, Dordogne…) et `TRAITS_FLEUVES_MM` ;
- noms des pays, façon carte murale Vidal-Lablache : Helvetica Neue Condensed Bold, en minuscules
  avec capitale, corps proportionnel à la taille du pays (`FACTEUR_TAILLE` × √surface visible, de 4 à
  30 mm, `TAILLE_NOM_MM`) ; les noms débordent librement sur les voisins et la mer, mais ne se
  touchent jamais entre eux (les grands pays d'abord, les petits réduits jusqu'à `REDUCTION_MAX`).
  Ils suivent le parallèle de leur point d'étiquette, avec la courbure de la vue ; si le nom couvre
  nettement mieux son pays le long de son axe principal (`GAIN_INCLINE`), il est écrit droit et
  incliné (Portugal, Royaume-Uni, Iran…). Les largeurs sont mesurées sur la police réelle (fontTools).
  Pas de nom sous `AIRE_MIN_NOM_MM2` (Luxembourg, Malte…). `POSITIONS` replace un nom à la main
  (lon, lat, angle éventuel), `NOMS` le coupe sur deux lignes, `NOMS_EXCLUS` le supprime.
  Les noms sont du texte modifiable dans les SVG (`textPath` pour les noms courbes), et des tracés
  vectoriels dans les PDF ;
- capitales des pays nommés (pas les micro-États) : rond pointé et nom en italique souligné
  (`CAPITALE_MM`, Helvetica Neue Bold Italic), placé autour du point (est, ouest, diagonales, nord,
  sud) sans toucher les autres noms ; les symboles sont réservés avant les noms de pays, qui se
  décalent un peu pour les éviter. `NOMS_CAPITALES` corrige un nom (Noursoultan → Astana) ;
- nettoyage : `AIRE_MIN_MM2` (îles et lacs minuscules), `FIN_MM` (filaments trop fins
  pour l'écran, surtout près de l'horizon), `TRAP_MM`, couleurs de l'aperçu.

## Sources

- Terres, lacs (dont la mer Caspienne) et fleuves (`ne_10m_rivers_lake_centerlines`) : Natural Earth 10m, version 5.1.1
  (dépôt nvkelso/natural-earth-vector, tag `v5.1.1`).
- Frontières (`ne_10m_admin_0_boundary_lines_land`) et noms des pays en français, avec leurs points
  d'étiquette (`ne_10m_admin_0_countries`), capitales (`ne_10m_populated_places`, « Admin-0 capital ») : même version. Frontières internationalement reconnues :
  les lignes « disputées » de Crimée et du détroit de Kertch et les lignes internes à Chypre sont écartées.
