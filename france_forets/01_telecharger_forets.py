"""Télécharge toutes les données nécessaires dans data/ :

- départements (gregoiredavid/france-geojson, commit figé)
- pays et lacs Natural Earth 10m
- couverture arborée ESA WorldCover 2021 (classe 10 = arbres) sur l'emprise
  de la France, via les aperçus (overviews) des COG distants
  -> data/arbres_wgs84.tif (uint8, 100 = arbre, 0 = autre, 255 = pas de donnée)

Les fichiers déjà présents ne sont pas retéléchargés.
"""
import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.io import MemoryFile

ICI = Path(__file__).parent
DATA = ICI / "data"
SORTIE = DATA / "arbres_wgs84.tif"

DEPARTEMENTS = ("https://raw.githubusercontent.com/gregoiredavid/france-geojson/"
                "5d34ee6d0140c29f785fdb047d9329f1aab58833/departements.geojson")
NATURAL_EARTH = {
    "ne_countries": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip",
    "ne_lakes": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip",
}
URL = ("/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
       "v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif")
OVERVIEW = 3  # index d'aperçu : facteur 16 -> ~160 m

# Tuiles de 3°, nommées par leur coin sud-ouest
LATS = [39, 42, 45, 48, 51]
LONS = [-6, -3, 0, 3, 6, 9]


def nom(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def vecteurs():
    dest = DATA / "departements.geojson"
    if not dest.exists():
        print("Départements…")
        urllib.request.urlretrieve(DEPARTEMENTS, dest)
    for dossier, url in NATURAL_EARTH.items():
        if not (DATA / dossier).exists():
            print(f"Natural Earth {dossier}…")
            with urllib.request.urlopen(url) as r:
                zipfile.ZipFile(io.BytesIO(r.read())).extractall(DATA / dossier)


def arbres():
    if SORTIE.exists():
        return
    print("Couverture arborée WorldCover…")
    memfiles = []
    for lat in LATS:
        for lon in LONS:
            n = nom(lat, lon)
            try:
                src = rasterio.open(URL.format(n), OVERVIEW_LEVEL=OVERVIEW)
            except rasterio.errors.RasterioIOError:
                print(f"  {n} : absente (mer)")
                continue
            with src:
                a = src.read(1)
                prof = src.profile
                prof.update(dtype="uint8", nodata=255, compress="deflate",
                            width=a.shape[1], height=a.shape[0],
                            transform=src.transform)
            arbres = np.where(a == 0, 255, np.where(a == 10, 100, 0)).astype("uint8")
            mf = MemoryFile()
            with mf.open(**prof) as dst:
                dst.write(arbres, 1)
            memfiles.append(mf)
            print(f"  {n} : {a.shape}, {100 * (a == 10).mean():.0f} % arbres")

    srcs = [m.open() for m in memfiles]
    mosaique, transform = merge(srcs, nodata=255)
    prof = srcs[0].profile
    prof.update(width=mosaique.shape[2], height=mosaique.shape[1],
                transform=transform, tiled=True)
    with rasterio.open(SORTIE, "w", **prof) as dst:
        dst.write(mosaique)
    print("Écrit", SORTIE, mosaique.shape)


def main():
    DATA.mkdir(exist_ok=True)
    vecteurs()
    arbres()


if __name__ == "__main__":
    main()
