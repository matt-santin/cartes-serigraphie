"""Télécharge les données nécessaires dans data/ :

- terres Natural Earth 10m (ne_10m_land)
- lacs Natural Earth 10m (ne_10m_lakes), dont la mer Caspienne
- fleuves et rivières Natural Earth 10m (ne_10m_rivers_lake_centerlines)
- pays (noms, points d'étiquette) et frontières terrestres Natural Earth 10m
- villes Natural Earth 10m (capitales et grandes villes)
- routes Natural Earth 10m (ne_10m_roads : grands axes)
- glaciers Natural Earth 10m (ne_10m_glaciated_areas : Groenland, Svalbard, Islande…)
- banquise arctique : étendue mensuelle du NSIDC Sea Ice Index (G02135, v4.0), un
  polygone par mois de 2015 à 2024 (120 fichiers), dans data/banquise/
- altitudes : ETOPO 2022 v1 (NOAA NCEI), surface à 60″ (~1,8 km), lue à distance et
  découpée sur l'emprise utile -> data/etopo_europe.tif (int16, mètres)

Version figée : Natural Earth 5.1.1, via le dépôt nvkelso/natural-earth-vector.
Les fichiers déjà présents ne sont pas retéléchargés.
"""
import io
import urllib.request
import zipfile

import numpy as np
import rasterio
from rasterio.windows import from_bounds
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
    "ne_10m_roads": "10m_cultural",
    "ne_10m_glaciated_areas": "10m_physical",
}
EXTENSIONS = [".shp", ".shx", ".dbf", ".prj", ".cpg"]

BANQUISE = ("https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/shapefiles/shp_extent/"
            "{mois:02d}_{nom}/extent_N_{annee}{mois:02d}_polygon_v4.0.zip")
MOIS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ANNEES_BANQUISE = range(2015, 2025)

ETOPO = ("/vsicurl/https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/"
         "60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif")
EMPRISE_ETOPO = (-40, 22, 75, 90)   # lon min, lat min, lon max, lat max


def main():
    for couche, theme in COUCHES.items():
        dossier = DATA / couche
        dossier.mkdir(parents=True, exist_ok=True)
        for ext in EXTENSIONS:
            dest = dossier / f"{couche}{ext}"
            if not dest.exists():
                print(f"{couche}{ext}…")
                urllib.request.urlretrieve(f"{BASE}{theme}/{couche}{ext}", dest)

    dossier = DATA / "banquise"
    for annee in ANNEES_BANQUISE:
        for mois, nom in enumerate(MOIS, start=1):
            dest = dossier / f"extent_N_{annee}{mois:02d}"
            if not dest.exists():
                print(f"banquise {annee}-{mois:02d}…")
                with urllib.request.urlopen(BANQUISE.format(mois=mois, nom=nom, annee=annee)) as r:
                    zipfile.ZipFile(io.BytesIO(r.read())).extractall(dest)

    dest = DATA / "etopo_europe.tif"
    if not dest.exists():
        print("altitudes ETOPO 2022…")
        with rasterio.open(ETOPO) as src:
            fenetre = from_bounds(*EMPRISE_ETOPO, transform=src.transform).round_offsets().round_lengths()
            z = src.read(1, window=fenetre)
            prof = src.profile
            prof.update(width=z.shape[1], height=z.shape[0], transform=src.window_transform(fenetre),
                        dtype="int16", nodata=-32768, crs="EPSG:4326", compress="deflate", predictor=2)
        z = np.where(z == src.nodata, -32768, np.round(z)).astype("int16")
        with rasterio.open(dest, "w", **prof) as dst:
            dst.write(z, 1)
        print("  ", dest.name, z.shape)


if __name__ == "__main__":
    main()
