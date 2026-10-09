"""Télécharge les données propres à la carte des eaux de France dans data/ :

- couverture arborée ESA WorldCover 2021 (v200, classe 10 = arbres), lue à ~320 m
  sur les aperçus (overviews) des COG distants, pour les tuiles de 3° visibles sur
  la carte -> data/arbres_wgs84.tif (uint8, 100 = arbre, 0 = autre, 255 = pas de donnée)
- bassins versants HydroBASINS v1c, niveau 8, région eu : plus fins que le niveau 5
  de la carte d'Europe, qui regroupe des fleuves côtiers (la Somme avec l'Escaut)
  -> data/bassins_eu_lev08/
- altitudes ETOPO 2022 v1 à 30″ (~900 m), lues à distance et découpées sur l'emprise
  utile -> data/etopo_30s.tif (int16, mètres)

Le reste est partagé avec les autres cartes, et téléchargé par leurs scripts s'il manque :
- ../europe/data : terres, lacs, pays, frontières, fleuves Natural Earth 10m
- ../europe_eaux/data : rivières HydroRIVERS, bassins HydroBASINS, mers de l'OHI
- ../france_forets/data : départements (IGN), lacs d'Europe, Léman et lac de Constance (OSM)
Les fichiers déjà présents ne sont pas retéléchargés.
"""
import importlib.util
import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.windows import from_bounds

ICI = Path(__file__).parent
DATA = ICI / "data"
RACINE = ICI.parent

WORLDCOVER = ("/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
              "v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif")
OVERVIEW = 4                     # index d'aperçu : facteur 32 -> ~320 m
ETOPO = ("/vsicurl/https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/30s/"
         "30s_surface_elev_gtif/ETOPO_2022_v1_30s_N90W180_surface.tif")
BASSINS = "https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_eu_lev08_v1c.zip"
EMPRISE = (-25, 27, 28, 63)      # lon min, lat min, lon max, lat max : tout ce que la carte montre


def module(chemin):
    spec = importlib.util.spec_from_file_location(f"{chemin.parent.name}_{chemin.stem[3:]}", chemin)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def tuiles_visibles():
    """Tuiles WorldCover de 3° (coin sud-ouest) dont un point tombe sur la carte."""
    carte = module(ICI / "02_carte.py")
    vers_nsper = Transformer.from_crs(carte.GEO, carte.NSPER, always_xy=True)
    demi_l = carte.LARGEUR_KM * 1000 / 2
    demi_h = demi_l * (carte.PAGE_H - 2 * carte.MARGE) / (carte.PAGE_L - 2 * carte.MARGE)
    cx, cy = (v * 1000 for v in carte.CENTRE_KM)
    tuiles = []
    for lat in range(EMPRISE[1] // 3 * 3, EMPRISE[3], 3):
        for lon in range(EMPRISE[0] // 3 * 3, EMPRISE[2], 3):
            la, lo = np.meshgrid(np.linspace(lat, lat + 3, 7), np.linspace(lon, lon + 3, 7))
            x, y = vers_nsper.transform(lo.ravel(), la.ravel())
            if (np.isfinite(x) & (np.abs(x - cx) < demi_l) & (np.abs(y - cy) < demi_h)).any():
                tuiles.append((lat, lon))
    return tuiles


def nom_tuile(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def arbres():
    dest = DATA / "arbres_wgs84.tif"
    if dest.exists():
        return
    print("Couverture arborée WorldCover…")
    memfiles = []
    for lat, lon in tuiles_visibles():
        n = nom_tuile(lat, lon)
        try:
            src = rasterio.open(WORLDCOVER.format(n), OVERVIEW_LEVEL=OVERVIEW)
        except rasterio.errors.RasterioIOError:
            continue   # tuile absente : pleine mer
        with src:
            a = src.read(1)
            prof = src.profile
            prof.update(dtype="uint8", nodata=255, compress="deflate",
                        width=a.shape[1], height=a.shape[0], transform=src.transform)
        mf = MemoryFile()
        with mf.open(**prof) as dst:
            dst.write(np.where(a == 0, 255, np.where(a == 10, 100, 0)).astype("uint8"), 1)
        memfiles.append(mf)
        print(f"  {n} : {100 * (a == 10).mean():.0f} % arbres")
    srcs = [m.open() for m in memfiles]
    mosaique, transform = merge(srcs, nodata=255)
    prof = srcs[0].profile
    prof.update(width=mosaique.shape[2], height=mosaique.shape[1], transform=transform, tiled=True)
    with rasterio.open(dest, "w", **prof) as dst:
        dst.write(mosaique)
    print("  ", dest.name, mosaique.shape)


def relief():
    dest = DATA / "etopo_30s.tif"
    if dest.exists():
        return
    print("Altitudes ETOPO 2022 (30″)…")
    with rasterio.open(ETOPO) as src:
        fenetre = from_bounds(*EMPRISE, transform=src.transform).round_offsets().round_lengths()
        z = src.read(1, window=fenetre)
        prof = src.profile
        prof.update(width=z.shape[1], height=z.shape[0], transform=src.window_transform(fenetre),
                    dtype="int16", nodata=-32768, crs="EPSG:4326", compress="deflate", predictor=2,
                    tiled=True)
        z = np.where(z == src.nodata, -32768, np.round(z)).astype("int16")
    with rasterio.open(dest, "w", **prof) as dst:
        dst.write(z, 1)
    print("  ", dest.name, z.shape)


def main():
    DATA.mkdir(exist_ok=True)
    # données partagées : chaque carte télécharge les siennes
    for dossier, script, temoin in [("europe", "01_telecharger_europe.py", "etopo_europe.tif"),
                                    ("europe_eaux", "01_telecharger_eaux.py", "mers_iho.geojson"),
                                    ("france_forets", "01_telecharger_forets.py", "lacs_osm.geojson")]:
        if not (RACINE / dossier / "data" / temoin).exists():
            module(RACINE / dossier / script).main()
    dest = DATA / "bassins_eu_lev08"
    if not dest.exists():
        print("Bassins HydroBASINS niveau 8…")
        requete = urllib.request.Request(BASSINS, headers={"User-Agent": "Mozilla/5.0"})   # sinon 403
        with urllib.request.urlopen(requete) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(dest)
    relief()
    arbres()


if __name__ == "__main__":
    main()
