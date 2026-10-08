"""Télécharge les données hydrographiques dans data/ :

- rivières HydroRIVERS v1.0 (HydroSHEDS, WWF) : réseau hydrographique vectoriel, avec
  pour chaque tronçon le débit moyen et la surface drainée ; régions eu (Europe et
  Moyen-Orient), af (Afrique du Nord), si (Sibérie), as (Asie centrale)
- bassins versants HydroBASINS v1c, niveau 5 (Pfafstetter), mêmes régions : chaque
  bassin connaît son bassin principal (MAIN_BAS, celui du fleuve qui le draine
  jusqu'à la mer) et s'il est endoréique ; les lignes de partage des eaux en sont
  les limites
- mers et océans de l'OHI (IHO Sea Areas v3, Marine Regions / VLIZ, service WFS) :
  la mer où débouche chaque fleuve décide de son versant -> data/mers_iho.geojson

Le fond commun avec la carte politique (terres, lacs, glaciers, banquise, relief
ETOPO 2022, pays) est lu dans ../europe/data : il est téléchargé par
europe/01_telecharger_europe.py, lancé ici s'il manque.
Les fichiers déjà présents ne sont pas retéléchargés.
"""
import importlib.util
import io
import urllib.request
import zipfile
from pathlib import Path

ICI = Path(__file__).parent
DATA = ICI / "data"
EUROPE = ICI.parent / "europe"

REGIONS = ["eu", "af", "si", "as"]
NIVEAU_BASSINS = 5
RIVIERES = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_{r}_shp.zip"
MERS = ("https://geo.vliz.be/geoserver/MarineRegions/wfs?service=WFS&version=1.0.0&request=GetFeature"
        "&typeName=MarineRegions:iho&outputFormat=application/json&bbox=-45,15,80,90")
BASSINS = "https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_{r}_lev{n:02d}_v1c.zip"


def extraire(url, dest):
    if dest.exists():
        return
    print(f"{url.rsplit('/', 1)[1]}…")
    requete = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})   # sinon 403
    with urllib.request.urlopen(requete) as r:
        zipfile.ZipFile(io.BytesIO(r.read())).extractall(dest)


def main():
    for r in REGIONS:
        extraire(RIVIERES.format(r=r), DATA / f"rivieres_{r}")
        extraire(BASSINS.format(r=r, n=NIVEAU_BASSINS), DATA / f"bassins_{r}")

    dest = DATA / "mers_iho.geojson"
    if not dest.exists():
        print("mers de l'OHI…")
        urllib.request.urlretrieve(MERS, dest)

    if not (EUROPE / "data" / "etopo_europe.tif").exists():
        spec = importlib.util.spec_from_file_location("telecharger_europe", EUROPE / "01_telecharger_europe.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()


if __name__ == "__main__":
    main()
