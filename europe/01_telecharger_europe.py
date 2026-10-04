"""Télécharge les données nécessaires dans data/ :

- terres Natural Earth 10m (ne_10m_land)
- lacs Natural Earth 10m (ne_10m_lakes), dont la mer Caspienne
- fleuves et rivières Natural Earth 10m (ne_10m_rivers_lake_centerlines)
- pays (noms, points d'étiquette) et frontières terrestres Natural Earth 10m
- villes Natural Earth 10m (pour les capitales)

Version figée : Natural Earth 5.1.1, via le dépôt nvkelso/natural-earth-vector.
Les fichiers déjà présents ne sont pas retéléchargés.
"""
import urllib.request
from pathlib import Path

ICI = Path(__file__).parent
DATA = ICI / "data"

VERSION = "v5.1.1"
BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{VERSION}/"
COUCHES = {
    "ne_10m_land": "10m_physical",
    "ne_10m_lakes": "10m_physical",
    "ne_10m_rivers_lake_centerlines": "10m_physical",
    "ne_10m_admin_0_countries": "10m_cultural",
    "ne_10m_admin_0_boundary_lines_land": "10m_cultural",
    "ne_10m_populated_places": "10m_cultural",
}
EXTENSIONS = [".shp", ".shx", ".dbf", ".prj", ".cpg"]


def main():
    for couche, theme in COUCHES.items():
        dossier = DATA / couche
        dossier.mkdir(parents=True, exist_ok=True)
        for ext in EXTENSIONS:
            dest = dossier / f"{couche}{ext}"
            if not dest.exists():
                print(f"{couche}{ext}…")
                urllib.request.urlretrieve(f"{BASE}{theme}/{couche}{ext}", dest)


if __name__ == "__main__":
    main()
