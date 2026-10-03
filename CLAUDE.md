Ce CLAUDE.md a pour but de m'aider dans la création de calques pour la sérigraphie. 

Je veux principalement faire des cartes, avec des couches de peinture en sérigraphie classique. Tu trouveras des exemples dans le dossier /exemples. Je te donnerai des idées que j'ai de cartes, et le but sera d'en faire pour la sérigraphie.

Règles :
 - tu peux installer les librairies python qu'il faut, avec le .venv associé. 
 - je ne veux pas que tu sortes de ce dossier au-delà du strict nécessaire
 - je veux les fichiers sortant en .svg et en .pdf

## Organisation du dépôt

- Un dossier par carte (ex. `france_forets/`), avec :
  - `01_telecharger_*.py` : télécharge toutes les données publiques dans `data/`, avec des sources figées (commit, version, id OSM) ;
  - `02_carte.py` : génère les calques dans `sortie/`, avec les réglages (format, seuils, épaisseurs) en tête de fichier ;
  - `README.md` : calques, ordre d'impression, sources, commandes.
- Seul le code est versionné : `data/`, `sortie/`, `exemples/` et `.venv/` sont ignorés. Les versions des librairies sont dans `requirements.txt`.
- Dépôt GitHub : https://github.com/matt-santin/cartes-serigraphie

## Conventions de sérigraphie retenues

- Une encre = un calque : SVG + PDF en noir 100 % sur blanc, même format de page, croix de repérage dans les 4 coins, nom du calque en marge. Plus un aperçu en couleur (un calque Inkscape par encre).
- Le noir des traits compte comme une encre à part entière.
- Débord (trapping) d'environ 0,3 mm des encres claires sous les plus foncées. Taches et trous de moins d'environ 0,8 mm² supprimés, contours lissés (pas de marches d'escalier de pixels).
- Export PDF via Inkscape en ligne de commande. Toujours vérifier le rendu en zoom (pdftoppm à 300 dpi et plus) : côtes, frontières, lacs.

## Cartes réalisées

- `france_forets/` : France des forêts, 500 × 500 mm, Lambert-93, 4 encres (mer bleue, terre, forêt vert foncé, noir pour départements et frontières). Forêt issue d'ESA WorldCover 2021 (densité locale ≥ 50 %), départements gregoiredavid/france-geojson, voisins et lacs Natural Earth, Léman et lac de Constance OSM. Les pays voisins reçoivent le même traitement que la France ; le cadrage reste centré sur la France.
