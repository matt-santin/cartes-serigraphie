"""Les eaux de France, vues de l'espace, en 6 calques de sérigraphie.

Même principe que la carte des eaux de l'Europe (europe_eaux/02_carte.py, dont on
réutilise les fonctions), mais centrée sur la France, avec le point de vue de Google
Maps en « vue globe » : projection perspective verticale depuis 1 834 km d'altitude
au-dessus de 46,49° N, 3,42° E, nord en haut (calé sur une capture d'écran : 36 villes
retrouvées à 1,2 pixel près). L'horizon est hors du cadre ; la rondeur de la Terre se
lit à la courbure du canevas et au léger resserrement des bords.

Les terres sont teintées selon le versant de la mer où elles s'écoulent, avec deux
encres claires transparentes qui se superposent (trois teintes) :
  Manche et mer Noire : jaune ; Atlantique et mer du Nord : rose ;
  Méditerranée : jaune + rose (orangé).

Ordre d'impression (la plus claire d'abord) :
  1. jaune  (jaune paille) ┐ terres hors forêts, selon le versant ; débord de 0,3 mm
  2. rose   (rose saumon)  ┘ sous le vert et le bleu ; neige en réserve
  3. forêt  (vert) : zones boisées (ESA WorldCover 2021, densité locale ≥ 50 %)
  4. relief (marron foncé) : estompage du relief en trame de points, par-dessus
  5. eaux   (bleu) : mers, lacs, rivières selon leur débit, noms des rivières ;
     noms des mers en réserve
  6. noir : lignes de partage des eaux, frontières, canevas, légende

Sorties dans sortie/ : un SVG et un PDF par calque (noir sur blanc),
plus un aperçu en couleur.
"""
import importlib.util
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import shapely
import shapely.ops
from pyproj import Transformer
from rasterio.warp import reproject, Resampling
from scipy import ndimage
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union

ICI = Path(__file__).parent
RACINE = ICI.parent
DATA = ICI / "data"
SORTIE = ICI / "sortie"
SORTIE.mkdir(exist_ok=True)
DATA_EUROPE = RACINE / "europe" / "data"
DATA_EAUX = RACINE / "europe_eaux" / "data"
DATA_FORETS = RACINE / "france_forets" / "data"


def module(chemin):
    spec = importlib.util.spec_from_file_location(f"{chemin.parent.name}_{chemin.stem[3:]}", chemin)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# fonctions des cartes d'Europe (projection, trame, SVG ; bassins et lignes de partage)
E = module(RACINE / "europe" / "02_carte.py")
EE = module(RACINE / "europe_eaux" / "02_carte.py")

# --- Point de vue ---------------------------------------------------------------
# point de vue de Google Maps en « vue globe » (capture du 8 octobre 2026), retrouvé par
# moindres carrés sur la position de 36 villes : écart moyen 1,2 pixel
LAT_0, LON_0 = 46.49, 3.42       # point à la verticale de la caméra
ALTITUDE_KM = 1834               # altitude de la caméra
CENTRE_KM = (-24, 27)            # centre du cadre dans la vue (km vers l'est, vers le nord)
R_TERRE = 6_371_000

# --- Paramètres d'impression (mm) -------------------------------------------
PAGE_L, PAGE_H = 800, 515        # format de la feuille (cadre 750 × 465 mm, proportions de la capture)
MARGE = 25
LARGEUR_KM = 1770                # largeur couverte par la carte (km), comme la capture
EMPRISE = (-25, 27, 28, 63)      # lon/lat de tout ce qui peut être visible (découpe des données)
# Estuaires : les départements IGN les comptent comme terre (la limite départementale passe au milieu
# de l'eau) ; dans ces cadres (lon/lat), limités à l'intérieur des estuaires pour ne pas toucher aux
# côtes ouvertes, l'eau de Natural Earth l'emporte. HydroRIVERS s'arrête au fond de l'estuaire.
ESTUAIRES = {"Gironde": (-1.05, 44.98, -0.5, 45.62), "Seine": (0.15, 49.38, 0.6, 49.5),
             "Loire": (-2.32, 47.17, -1.7, 47.32), "Somme": (1.5, 50.17, 1.7, 50.25)}

# Rivières (HydroRIVERS) : plus fines et plus nombreuses qu'à l'échelle de l'Europe
DEBIT_MIN = 3                    # m³/s
FACTEUR_RIVIERE = 0.15           # épaisseur (mm) = FACTEUR × débit^0,25 (Rhône ≈ 1 mm)
TRAIT_RIVIERE_MM = (0.2, 1.1)
PAS_TRAIT_MM = 0.05
LISSAGE_RIVIERE_MM = 0.08

# Forêt (ESA WorldCover 2021, comme france_forets/) : densité locale de couvert arboré
PAS_FORET_MM = 0.2               # grille de calcul
LISSAGE_FORET_MM = 0.5           # rayon de la densité locale
SEUIL_DENSITE = 0.50             # « boisé » au-delà de 50 % d'arbres alentour
AIRE_MIN_FORET_MM2 = 0.8

# Relief (ETOPO 2022 à 30″) : généralisé à l'échelle d'une cellule de trame (0,55 mm ≈ 1,3 km)
RAYON_RELIEF_M = 1_400_000       # grille de calcul : jusqu'aux coins du cadre
PIXEL_RELIEF_M = 700
LISSAGE_RELIEF_M = 1600
EXAGERATION = 4

# Partage des eaux : versants à la française (la Manche à part), grands bassins dès 8 000 km²
VERSANTS = {**EE.VERSANTS,
            "atlantique": [m for m in EE.VERSANTS["atlantique"] if m != "English Channel"],
            "Manche": ["English Channel"]}
BASSIN_MIN_KM2 = 8_000           # Seine, Loire, Garonne, Rhône, Rhin, Meuse, Escaut, Adour, Charente, Vilaine…
TRAIT_VERSANT_MM, TIRETS_VERSANT_MM = 0.7, (3.0, 1.2)
TRAIT_BASSIN_MM, TIRETS_BASSIN_MM = 0.45, (0.01, 1.1)
TRAIT_FRONTIERE_MM = 0.3
TRAIT_GRATICULE_MM = 0.2
PAS_GRATICULE = 2                # degrés

# Teintes des versants : encres de terre posées sur chacun (les deux = orangé)
TEINTES = {"Manche": ("jaune",), "mer Noire": ("jaune",), "atlantique": ("rose",),
           "mer du Nord": ("rose",), "méditerranée": ("jaune", "rose")}
LEGENDE_VERSANTS = [("Manche", "Manche"), ("Atlantique", "atlantique"), ("mer du Nord", "mer du Nord"),
                    ("Méditerranée", "méditerranée"), ("mer Noire", "mer Noire")]

# Noms des mers (lon, lat, corps mm[, angle]) : capitales italiques en réserve dans le bleu
MERS = [("OCÉAN ATLANTIQUE", -7.6, 48.25, 7.0), ("GOLFE DE GASCOGNE", -3.0, 44.9, 5.0),
        ("LA MANCHE", -2.0, 50.0, 7.0), ("MER DU NORD", 2.6, 51.62, 4.0),
        ("MER CELTIQUE", -7.2, 49.85, 4.5), ("MER MÉDITERRANÉE", 6.2, 42.1, 7.0),
        ("GOLFE DU LION", 4.0, 42.95, 5.0), ("MER LIGURE", 8.8, 43.75, 4.5),
        ("MER TYRRHÉNIENNE", 11.0, 41.6, 3.8, -45), ("MER ADRIATIQUE", 13.6, 44.5, 4.0, 38),
        ("PAS DE CALAIS", 1.55, 50.95, 2.8, -55)]
# Noms des rivières : un point sur leur cours ; le nom suit le tracé HydroRIVERS imprimé,
# au plus près de ce point. Corps selon le débit à cet endroit.
RIVIERES = {
    "Seine": (1.55, 49.1), "Loire": (0.75, 47.35), "Garonne": (0.55, 44.3), "Rhône": (4.75, 44.6),
    "Rhin": (7.65, 48.4), "Meuse": (5.4, 49.05), "Moselle": (6.2, 49.0), "Saône": (4.9, 46.6),
    "Dordogne": (0.6, 44.85), "Lot": (1.4, 44.45), "Tarn": (1.55, 43.95), "Marne": (3.55, 48.95),
    "Oise": (2.6, 49.35), "Aisne": (3.6, 49.4), "Yonne": (3.5, 47.85), "Allier": (3.35, 46.2),
    "Cher": (1.9, 47.25), "Vienne": (0.65, 46.55), "Charente": (0.0, 45.75), "Adour": (-0.6, 43.7),
    "Vilaine": (-1.85, 47.75), "Somme": (2.3, 49.95), "Escaut": (3.45, 50.45), "Durance": (5.6, 43.75),
    "Isère": (5.5, 45.2), "Doubs": (6.3, 47.25), "Aude": (2.6, 43.2), "Sarthe": (0.15, 47.85),
    "Mayenne": (-0.65, 47.85), "Indre": (1.4, 47.0), "Ain": (5.45, 46.2), "Loir": (0.6, 47.7),
    "Creuse": (1.0, 46.75), "Aveyron": (1.75, 44.15), "Eure": (1.35, 48.8), "Orne": (-0.35, 49.0),
    "Hérault": (3.45, 43.55), "Gave de Pau": (-0.75, 43.4), "Sambre": (4.0, 50.3),
    "Tamise": (-1.0, 51.6), "Severn": (-2.3, 52.2), "Trent": (-1.2, 53.0), "Shannon": (-8.3, 53.0),
    "Èbre": (-0.9, 41.65), "Douro": (-4.5, 41.6), "Tage": (-4.5, 39.9), "Segre": (0.9, 41.9),
    "Pô": (10.0, 45.05), "Adige": (11.3, 45.2), "Tessin": (8.8, 45.4), "Arno": (11.0, 43.75),
    "Tibre": (12.4, 42.5), "Aar": (7.5, 47.2), "Neckar": (9.2, 48.9), "Main": (9.5, 50.0),
    "Danube": (11.4, 48.75), "Inn": (12.3, 48.2), "Elbe": (11.8, 52.5), "Weser": (9.2, 52.4),
    "Ems": (7.3, 52.6), "Sarre": (6.9, 49.25), "Lahn": (8.2, 50.35), "Ruhr": (7.5, 51.42),
}
RIVIERE_MM = {300: 5.5, 80: 4.5, 0: 3.6}   # corps selon le débit (m³/s, ≥ clé)
ECART_NOM_MM = {300: 1.6, 80: 1.3, 0: 1.0}
CARTOUCHE = (MARGE + 5, 197, 116, 160)   # légende : x, y, largeur, hauteur (mm), en réserve dans l'océan
TRAIT_COUPE_MM = 0.6             # rivières plus fines : s'interrompent sous les noms ; plus épaisses : évitées
ECART_ARC_MM = 3.0               # méandres tolérés autour de l'arc qui porte le nom
RECHERCHE_KM = 160               # longueur de cours explorée de part et d'autre du point

COULEURS = {"jaune": "#ecd68e", "rose": "#efb8a0", "foret": "#7f9f5a", "relief": "#6b4a2b", "eaux": "#3d6f9e", "noir": "#1a1a1a"}

SPHERE = f"+R={R_TERRE} +units=m +no_defs"
AEQD = f"+proj=aeqd +lat_0={LAT_0} +lon_0={LON_0} {SPHERE}"
NSPER = f"+proj=nsper +h={ALTITUDE_KM * 1000} +lat_0={LAT_0} +lon_0={LON_0} {SPHERE}"
GEO = f"+proj=longlat {SPHERE}"

# les fonctions reprises lisent ces réglages dans leur module : on les remplace
for _m in (E, EE.E):
    for _nom in ("LAT_0", "LON_0", "ALTITUDE_KM", "PAGE_L", "PAGE_H", "MARGE", "AEQD", "NSPER"):
        setattr(_m, _nom, globals()[_nom])
for _nom, _val in {"PAGE_L": PAGE_L, "PAGE_H": PAGE_H, "MARGE": MARGE, "VERSANTS": VERSANTS,
                   "BASSIN_MIN_KM2": BASSIN_MIN_KM2}.items():
    setattr(EE, _nom, _val)

chaikin, arc, lignes_de, projeter = EE.chaikin, EE.arc, EE.lignes_de, E.projeter


def lire(chemin, **kw):
    return gpd.read_file(chemin, bbox=EMPRISE, **kw)


# --- Données ------------------------------------------------------------------------
def charger():
    zone = box(*EMPRISE)
    terres = unary_union(lire(DATA_EUROPE / "ne_10m_land" / "ne_10m_land.shp").geometry.buffer(0)).intersection(zone)
    terres_ne = terres
    # la France d'après les départements (IGN), plus précise que Natural Earth ; les
    # interstices entre les deux sources le long des frontières sont bouchés
    depts = gpd.read_file(DATA_FORETS / "departements.geojson")
    france = unary_union(depts.geometry.buffer(0))
    pays = lire(DATA_EUROPE / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp")
    france_ne = unary_union(pays[pays.ADMIN == "France"].geometry.buffer(0))
    voisins = terres.difference(france_ne)
    joint = voisins.buffer(0.02).intersection(france.buffer(0.15))
    terres = unary_union([voisins, joint, france])
    for b in ESTUAIRES.values():
        terres = terres.difference(box(*b).difference(terres_ne))

    osm = gpd.read_file(DATA_FORETS / "lacs_osm.geojson")
    ne = lire(DATA_EUROPE / "ne_10m_lakes" / "ne_10m_lakes.shp")
    ne = ne[~ne.name.isin(["Lake Geneva", "Bodensee"])]   # remplacés par OSM
    ne_eu = lire(DATA_FORETS / "ne_lakes_europe" / "ne_10m_lakes_europe.shp")
    lacs = unary_union(list(osm.geometry.buffer(0)) + list(ne.geometry.buffer(0)) + list(ne_eu.geometry.buffer(0)))

    lignes = lire(DATA_EUROPE / "ne_10m_admin_0_boundary_lines_land" / "ne_10m_admin_0_boundary_lines_land.shp")
    lignes = lignes[lignes.FEATURECLA == "International boundary (verify)"]
    frontieres = unary_union(list(lignes.geometry)).intersection(zone)

    riv = pd.concat([lire(f, where=f"DIS_AV_CMS >= {DEBIT_MIN}",
                          columns=["HYRIV_ID", "NEXT_DOWN", "UPLAND_SKM", "DIS_AV_CMS", "LENGTH_KM"])
                     for f in sorted(DATA_EAUX.glob("rivieres_*/*/*.shp"))
                     if any(r in f.name for r in ("_eu", "_af"))], ignore_index=True)
    ep = np.clip(FACTEUR_RIVIERE * riv.DIS_AV_CMS ** 0.25, *TRAIT_RIVIERE_MM)
    riv["trait"] = np.round(ep / PAS_TRAIT_MM) * PAS_TRAIT_MM
    print(f"   rivières : {len(riv):,} tronçons de plus de {DEBIT_MIN} m³/s")
    return terres, lacs, frontieres, riv


def reseau(riv):
    """Index du réseau : tronçons par identifiant, et pour chaque tronçon son affluent
    principal (celui qui draine la plus grande surface)."""
    par_id = riv.set_index("HYRIV_ID", drop=False)
    amont = riv.sort_values("UPLAND_SKM").groupby("NEXT_DOWN").HYRIV_ID.last()
    return par_id, amont


def cours(riv, par_id, amont_de, lon, lat):
    """Le cours principal de la rivière qui passe près de (lon, lat) : tronçon le plus
    proche, prolongé vers l'aval et vers l'amont (affluent le plus grand) sur
    RECHERCHE_KM. Renvoie (ligne géographique, débit au point)."""
    pt = Point(lon, lat)
    proches = riv.iloc[riv.sindex.query(pt.buffer(0.15))]
    if proches.empty:
        return None, 0
    # le plus proche, en favorisant les gros cours d'eau (le point est approximatif)
    d = pd.Series(shapely.distance(proches.geometry.values, pt), proches.index) - 0.02 * np.log10(proches.DIS_AV_CMS)
    depart = proches.loc[d.idxmin()]
    aval, km, i = [], 0, depart.NEXT_DOWN
    while i in par_id.index and km < RECHERCHE_KM:
        t = par_id.loc[i]
        aval.append(t.geometry)
        km += t.LENGTH_KM
        i = t.NEXT_DOWN
    amont, km, i = [], 0, depart.HYRIV_ID
    while i in amont_de.index and km < RECHERCHE_KM:
        t = par_id.loc[amont_de[i]]
        amont.append(t.geometry)
        km += t.LENGTH_KM
        i = t.HYRIV_ID
    ligne = shapely.line_merge(unary_union(amont[::-1] + [depart.geometry] + aval))
    if not isinstance(ligne, LineString):
        ligne = max(lignes_de(ligne), key=lambda l: l.length)
    return ligne, depart.DIS_AV_CMS


def melange(c1, c2):
    """Couleur de deux encres transparentes superposées (multiplication)."""
    a, b = (np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)]) for c in (c1, c2))
    return "#" + "".join(f"{v:02x}" for v in (a * b // 255))


def sans_petits(m, px_min):
    """Masque sans ses composantes de moins de px_min pixels."""
    lab, n = ndimage.label(m)
    tailles = np.bincount(lab.ravel())
    garder = tailles >= px_min
    garder[0] = False
    return garder[lab]


def foret_carte(forme, t_nsper):
    """Densité de couvert arboré (0-1) sur la grille de la carte."""
    dens = np.full(forme, np.nan, dtype="float32")
    with rasterio.open(DATA / "arbres_wgs84.tif") as src:
        a = src.read(1).astype("float32")
        a[a == 255] = np.nan
        reproject(a, dens, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=t_nsper, dst_crs=NSPER, dst_nodata=np.nan, resampling=Resampling.average)
    return ndimage.gaussian_filter(np.nan_to_num(dens / 100), LISSAGE_FORET_MM / PAS_FORET_MM)


def relief_carte(rayon, forme, t_nsper):
    """Altitude (m) et intensité de l'ombre (0-1) sur la grille de la carte : estompage
    calculé en azimutale équidistante, puis reporté dans la perspective (comme europe/)."""
    with rasterio.open(DATA / "etopo_30s.tif") as src:
        z_src, t_src, crs_src = src.read(1).astype("float32"), src.transform, src.crs
    z_src[z_src == -32768] = np.nan
    n = int(2 * rayon / PIXEL_RELIEF_M)
    t_aeqd = rasterio.transform.from_origin(-rayon, rayon, PIXEL_RELIEF_M, PIXEL_RELIEF_M)
    z = np.full((n, n), np.nan, dtype="float32")
    reproject(z_src, z, src_transform=t_src, src_crs=crs_src, src_nodata=np.nan,
              dst_transform=t_aeqd, dst_crs=AEQD, dst_nodata=np.nan, resampling=Resampling.bilinear)
    z0 = ndimage.gaussian_filter(np.nan_to_num(np.maximum(z, 0)), LISSAGE_RELIEF_M / PIXEL_RELIEF_M) * EXAGERATION
    dzdy, dzdx = np.gradient(z0, PIXEL_RELIEF_M)
    pente = np.arctan(np.hypot(dzdx, dzdy))
    az, haut = np.radians(E.SOLEIL[0]), np.radians(E.SOLEIL[1])
    zen = np.pi / 2 - haut
    descente = np.arctan2(-dzdx, dzdy)
    eclairage = np.cos(zen) * np.cos(pente) + np.sin(zen) * np.sin(pente) * np.cos(az - descente)
    ombre = np.clip((np.cos(zen) - eclairage) / np.cos(zen), 0, 1).astype("float32")
    del z, z0, dzdx, dzdy, pente, descente, eclairage
    ombre_carte = np.zeros(forme, dtype="float32")
    reproject(ombre, ombre_carte, src_transform=t_aeqd, src_crs=AEQD,
              dst_transform=t_nsper, dst_crs=NSPER, dst_nodata=0, resampling=Resampling.bilinear)
    z_carte = np.zeros(forme, dtype="float32")
    reproject(np.nan_to_num(z_src), z_carte, src_transform=t_src, src_crs=crs_src,
              dst_transform=t_nsper, dst_crs=NSPER, dst_nodata=0, resampling=Resampling.bilinear)
    return z_carte, ombre_carte


# --- Principal ----------------------------------------------------------------------
def main():
    calotte, theta = E.calotte_visible()
    print(f"Horizon à {theta:.1f}° du centre de la vue")
    terres, lacs, frontieres, riv = charger()
    bassins = EE.charger_bassins(sorted((DATA / "bassins_eu_lev08").glob("*.shp")))
    geo_vers_aeqd = Transformer.from_crs(GEO, AEQD, always_xy=True)
    aeqd_vers_nsper = Transformer.from_crs(AEQD, NSPER, always_xy=True)

    # mise en page : le point CENTRE_KM de la vue au centre du cadre
    cadre_l, cadre_h = PAGE_L - 2 * MARGE, PAGE_H - 2 * MARGE
    ech = LARGEUR_KM * 1000 / cadre_l          # m par mm
    ox, oy = CENTRE_KM[0] * 1000, CENTRE_KM[1] * 1000
    print(f"Au centre de la vue : 1 mm = {ech / 1000:.2f} km  (1:{ech * 1000:,.0f})")

    def vers_mm(g):
        g = shapely.affinity.translate(g, -ox, -oy)
        g = shapely.affinity.scale(g, 1 / ech, -1 / ech, origin=(0, 0))
        return shapely.affinity.translate(g, MARGE + cadre_l / 2, MARGE + cadre_h / 2)

    def geo_mm(g, surface=False):
        g = projeter(g, geo_vers_aeqd)
        g = (g.buffer(0) if surface else g).intersection(calotte)
        if g.is_empty:
            return g
        g = vers_mm(projeter(g, aeqd_vers_nsper))
        return g.buffer(0) if surface else g

    print("Projection…")
    cadre = box(MARGE, MARGE, PAGE_L - MARGE, PAGE_H - MARGE)
    horizon = vers_mm(projeter(calotte.segmentize(R_TERRE * 0.002), aeqd_vers_nsper))
    globe_mm = horizon.intersection(cadre)
    r = E.FIN_MM / 2
    terres_mm = geo_mm(terres.difference(lacs), surface=True).simplify(E.SIMPLIF_MM).buffer(0)
    terres_mm = terres_mm.buffer(-r).buffer(r).buffer(r).buffer(-r)
    bande = horizon.difference(horizon.buffer(-3 * r))
    terres_mm = terres_mm.union(terres_mm.buffer(3 * r).intersection(bande))
    terres_mm = E.nettoyer(terres_mm.intersection(globe_mm), E.AIRE_MIN_MM2)
    mer_mm = E.nettoyer(globe_mm.difference(terres_mm), E.AIRE_MIN_MM2)
    terre_nette = globe_mm.difference(mer_mm)

    # --- relief et neige
    print("Relief…")
    forme = (int(cadre_h / PAS_FORET_MM), int(cadre_l / PAS_FORET_MM))
    t_nsper = rasterio.transform.from_origin(ox - cadre_l / 2 * ech, oy + cadre_h / 2 * ech,
                                             PAS_FORET_MM * ech, PAS_FORET_MM * ech)
    t_mm = rasterio.Affine(PAS_FORET_MM, 0, MARGE, 0, PAS_FORET_MM, MARGE)
    z_carte, ombre_carte = relief_carte(RAYON_RELIEF_M, forme, t_nsper)
    neige_mm = E.nettoyer(E.vectoriser(z_carte >= E.NEIGE_M, t_mm).intersection(terre_nette), E.AIRE_MIN_MM2)
    print(f"   neige au-dessus de {E.NEIGE_M} m : {neige_mm.area:.0f} mm²")

    # --- forêt
    print("Forêt…")
    dens = foret_carte(forme, t_nsper)
    px_min = int(AIRE_MIN_FORET_MM2 / PAS_FORET_MM ** 2)
    masque = dens >= SEUIL_DENSITE
    masque = sans_petits(masque, px_min)
    masque = ~sans_petits(~masque, px_min)
    foret_mm = E.vectoriser(masque, t_mm).intersection(terre_nette).difference(neige_mm)
    foret_mm = E.nettoyer(foret_mm, AIRE_MIN_FORET_MM2)
    print(f"   {len(foret_mm.geoms)} massifs, {100 * foret_mm.area / terre_nette.area:.0f} % des terres")

    # --- versants : chaque sous-bassin rastérisé sur la grille de la carte avec son
    # versant (trous et mer : versant le plus proche), puis une zone par encre de terre
    print("Versants…")
    noms_v = sorted(bassins.versant.dropna().unique())
    formes = [(geo_mm(g, surface=True), noms_v.index(v) + 1)
              for g, v in zip(bassins.geometry, bassins.versant) if isinstance(v, str)]
    V = rasterio.features.rasterize([(g, k) for g, k in formes if not g.is_empty],
                                    out_shape=forme, transform=t_mm, dtype="int32")
    V = V[tuple(ndimage.distance_transform_edt(V == 0, return_distances=False, return_indices=True))]
    zones = {}
    for encre in ("jaune", "rose"):
        # (un versant absent de TEINTES prend le jaune)
        ks = [i + 1 for i, v in enumerate(noms_v) if encre in TEINTES.get(v, ("jaune",))]
        zones[encre] = E.vectoriser(np.isin(V, ks), t_mm)
    print("   " + ", ".join(f"{v} : {'+'.join(TEINTES.get(v, ('jaune',)))}" for v in noms_v
                             if (V == noms_v.index(v) + 1).any()))

    # débords : les terres passent 0,3 mm sous le vert et sous le bleu, le vert sous le bleu ;
    # entre le jaune et le rose, chacun déborde de 0,15 mm sur l'autre
    eau_aplat = globe_mm.difference(terre_nette)
    foret_imp = E._polygones(foret_mm.buffer(E.TRAP_MM).intersection(foret_mm.union(eau_aplat)))
    terre_imp = (terre_nette.difference(neige_mm).difference(foret_mm.buffer(-E.TRAP_MM))
                 .buffer(E.TRAP_MM).intersection(globe_mm).difference(neige_mm)
                 .difference(foret_mm.buffer(-E.TRAP_MM)))
    teintes_imp = {encre: E.nettoyer(terre_imp.intersection(z.buffer(E.TRAP_MM / 2)), E.AIRE_MIN_MM2)
                   for encre, z in zones.items()}
    zone_relief = terre_nette.difference(neige_mm.buffer(0.2))
    trame_d = E.trame(ombre_carte, t_mm, zone_relief)

    # --- rivières
    print("Rivières…")
    terre_libre = terre_nette
    shapely.prepare(terre_libre)
    rivieres_mm = []
    for t, g in riv.groupby("trait"):
        g = geo_mm(shapely.multilinestrings(list(g.geometry)))
        g = shapely.line_merge(g.intersection(terre_libre))
        g = shapely.MultiLineString([LineString(chaikin(np.asarray(l.simplify(LISSAGE_RIVIERE_MM).coords), 2))
                                     for l in lignes_de(g) if len(l.coords) > 1])
        if not g.is_empty:
            rivieres_mm.append((t, g))
    eau_occupee = unary_union([g.buffer(t / 2 + 0.25, quad_segs=2) for t, g in rivieres_mm
                               if t > TRAIT_COUPE_MM])
    shapely.prepare(eau_occupee)

    print("Partage des eaux…")
    partage_versants, partage_bassins = EE.lignes_partage(bassins, lambda g: geo_mm(g, surface=True), terre_nette)
    frontieres_mm = geo_mm(frontieres).intersection(terre_nette)

    # --- noms des mers, en réserve dans le bleu
    print("Noms…")
    occupe, defs = [], []
    mesure_mer = E.mesureur(*EE.MER_POLICE)
    rayon_calotte = calotte.bounds[2]

    def parallele_mm(lon, lat):
        lons = np.arange(lon - 30, lon + 30, 0.02)
        x, y = geo_vers_aeqd.transform(lons, np.full_like(lons, lat))
        ok = np.hypot(x, y) < rayon_calotte
        xs, ys = aeqd_vers_nsper.transform(x[ok], y[ok])
        return vers_mm(LineString(np.column_stack([xs, ys])))

    def point_mm(lon, lat):
        return vers_mm(projeter(projeter(Point(lon, lat), geo_vers_aeqd), aeqd_vers_nsper))

    mer_libre = mer_mm.buffer(-0.8)
    mers_svg = []
    for k, (nom, lon, lat, t0, *angle) in enumerate(MERS):
        pt = point_mm(lon, lat)
        if not globe_mm.contains(pt):
            print(f"   ({nom} hors de la carte)")
            continue
        place = None
        for t in (t0, 0.9 * t0, 0.8 * t0, 0.7 * t0):
            L = mesure_mer(nom) * t + EE.ESPACEMENT_MER * t * (len(nom) - 1)
            for d in (0, -0.6 * t, 0.6 * t, -1.2 * t, 1.2 * t):
                if angle:
                    a = np.radians(angle[0])
                    cx, cy = pt.x - np.sin(a) * d, pt.y + np.cos(a) * d
                    e = shapely.affinity.rotate(box(cx - L / 2, cy - 0.4 * t, cx + L / 2, cy + 0.4 * t),
                                                angle[0], origin=(cx, cy))
                    base = None
                else:
                    ligne = parallele_mm(lon, lat).offset_curve(d) if d else parallele_mm(lon, lat)
                    if not isinstance(ligne, LineString):
                        continue
                    s0 = ligne.project(pt)
                    if s0 - L / 2 < 0 or s0 + L / 2 > ligne.length:
                        continue
                    seg = shapely.ops.substring(ligne, s0 - L / 2, s0 + L / 2)
                    e = seg.buffer(0.4 * t, cap_style="flat")
                    base = seg.offset_curve(0.36 * t)
                if mer_libre.contains(e) and not any(e.intersects(o) for o in occupe):
                    place = (t, e, base, cx if angle else None, cy if angle else None)
                    break
            if place:
                break
        if not place:
            print(f"   (pas de place pour {nom!r})")
            continue
        t, e, base, cx, cy = place
        occupe.append(e)
        style = (f'font-family="Helvetica Neue" font-weight="bold" font-style="italic" font-size="{t:.2f}" '
                 f'letter-spacing="{EE.ESPACEMENT_MER * t:.2f}" fill="#ffffff"')
        if base is None:
            mers_svg.append(f'<text {style} text-anchor="middle" x="{cx:.2f}" y="{cy + 0.36 * t:.2f}" '
                            f'transform="rotate({angle[0]} {cx:.2f} {cy:.2f})">{nom}</text>')
        else:
            defs.append(f'<path id="mer{k}" d="{E.d_lignes(base)}"/>')
            mers_svg.append(f'<text {style}><textPath xlink:href="#mer{k}">{nom}</textPath></text>')
    print(f"   {len(mers_svg)} noms de mers")

    # --- noms des rivières, le long du tracé imprimé, du plus gros débit au plus petit
    mesure_riv = E.mesureur(*EE.RIVIERE_POLICE)
    terre_texte = terre_nette.buffer(-0.3)
    shapely.prepare(terre_texte)
    candidats = []
    par_id, amont_de = reseau(riv)
    for nom, (lon, lat) in RIVIERES.items():
        if not globe_mm.contains(point_mm(lon, lat)):
            continue   # hors du cadre
        ligne, debit = cours(riv, par_id, amont_de, lon, lat)
        if ligne is None:
            print(f"   (rivière introuvable : {nom})")
            continue
        g = geo_mm(ligne).intersection(globe_mm)
        parts = lignes_de(g)
        if parts:
            candidats.append((debit, nom, max(parts, key=lambda l: l.length), point_mm(lon, lat)))
    noms_riv_svg, noms_riv_e = [], []
    for debit, nom, ligne, pt in sorted(candidats, key=lambda c: -c[0]):
        t = next(v for k, v in sorted(RIVIERE_MM.items(), reverse=True) if debit >= k)
        blanc = next(v for k, v in sorted(ECART_NOM_MM.items(), reverse=True) if debit >= k)
        L = mesure_riv(nom) * t + EE.ESPACEMENT_RIVIERE * t * (len(nom) - 1)
        lisse = LineString(chaikin(np.asarray(ligne.simplify(0.5).coords), 3))
        if lisse.length < L + 4:
            print(f"   (trop court pour son nom : {nom})")
            continue
        s_pt = lisse.project(pt)
        sa_riviere = ligne.buffer(0.4)   # le nom ne passe jamais sur sa propre rivière
        positions = np.arange(L / 2 + 1, lisse.length - L / 2 - 1, 1.0)
        positions = positions[np.argsort(np.abs(positions - s_pt))]
        choix = None
        for s in positions:
            seg_brut = shapely.ops.substring(lisse, s - L / 2, s + L / 2)
            c = np.asarray(seg_brut.coords)
            if c[-1, 0] < c[0, 0]:
                seg_brut = seg_brut.reverse()
            seg, ecart_max = arc(seg_brut)
            if ecart_max > ECART_ARC_MM:
                continue
            if np.hypot(*np.subtract(seg.coords[-1], seg.coords[0])) < EE.COURBURE_MAX * seg.length:
                continue
            # d'abord au plus près ; le nom s'écarte si la rivière méandre jusqu'à lui
            for cote, plus in [(c, p) for p in np.arange(0, ecart_max + 0.01, 0.5) for c in (-1, 1)]:
                ecart = blanc + plus + (0 if cote < 0 else 0.72 * t)
                base = arc(seg_brut, prolonge=L / 2)[0].offset_curve(cote * ecart)
                if not isinstance(base, LineString):
                    continue
                e = seg.offset_curve(cote * (blanc + plus + 0.36 * t)).buffer(0.42 * t, cap_style="flat")
                if (terre_texte.contains(e) and not eau_occupee.intersects(e) and not sa_riviere.intersects(e)
                        and not any(e.intersects(o) for o in occupe)):
                    choix = (base, e)
                    break
            if choix:
                break
        if not choix:
            print(f"   (pas de place pour {nom})")
            continue
        base, e = choix
        occupe.append(e)
        noms_riv_e.append(e)
        ident = f"riv{len(noms_riv_svg)}"
        defs.append(f'<path id="{ident}" d="{E.d_lignes(base)}"/>')
        noms_riv_svg.append(
            f'<text font-family="Helvetica Neue" font-weight="500" font-style="italic" font-size="{t:.2f}" '
            f'letter-spacing="{EE.ESPACEMENT_RIVIERE * t:.2f}" text-anchor="middle" fill="{{c}}">'
            f'<textPath xlink:href="#{ident}" startOffset="50%">{nom}</textPath></text>')
    print(f"   {len(noms_riv_svg)} noms de rivières (sur {len(candidats)} dans le cadre)")
    if noms_riv_e:
        halo = unary_union(noms_riv_e).buffer(0.3)
        rivieres_mm = [(t, g if t > TRAIT_COUPE_MM else g.difference(halo)) for t, g in rivieres_mm]

    # --- légende, dans un cartouche en réserve (papier) dans l'océan
    legende_svg, legende_eaux_svg = [], []
    x0, y0, pas = CARTOUCHE[0] + 5, CARTOUCHE[1] + 11, 8.0

    def texte_leg(x, y, texte, gras=False):
        style = 'font-weight="bold" font-stretch="condensed" font-size="6.5"' if gras else 'font-size="4.2"'
        return f'<text x="{x:.2f}" y="{y:.2f}" font-family="Helvetica Neue" {style} fill="{{c}}">{texte}</text>'

    legende_svg.append(texte_leg(x0, y0, "Lignes de partage des eaux", gras=True))
    for i, (texte, ep, tirets, cap) in enumerate([
            ("entre les versants des mers", TRAIT_VERSANT_MM, TIRETS_VERSANT_MM, "butt"),
            ("entre les grands bassins fluviaux", TRAIT_BASSIN_MM, TIRETS_BASSIN_MM, "round"),
            ("frontières", TRAIT_FRONTIERE_MM, None, "round")]):
        y = y0 + (i + 1) * pas
        dash = f' stroke-dasharray="{tirets[0]} {tirets[1]}"' if tirets else ""
        legende_svg.append(f'<path d="M{x0:.2f},{y - 1.6:.2f}h20" stroke="{{c}}" stroke-width="{ep}"{dash} '
                           f'stroke-linecap="{cap}" fill="none"/>' + texte_leg(x0 + 25, y, texte))
    y1 = y0 + 4.5 * pas
    legende_svg.append(texte_leg(x0, y1, "Rivières selon leur débit moyen", gras=True))
    for i, debit in enumerate((5, 100, 1500)):
        y = y1 + (i + 1) * pas
        ep = float(np.clip(FACTEUR_RIVIERE * debit ** 0.25, *TRAIT_RIVIERE_MM))
        legende_eaux_svg.append(f'<path d="M{x0:.2f},{y - 1.6:.2f}h20" stroke="{{c}}" stroke-width="{ep:.2f}" '
                                f'stroke-linecap="round" fill="none"/>')
        legende_svg.append(texte_leg(x0 + 25, y, f"{debit:,} m³/s".replace(",", " ")))
    y2 = y1 + 4.5 * pas
    legende_svg.append(texte_leg(x0, y2, "Forêts", gras=True))
    legende_foret = f'<rect x="{x0:.2f}" y="{y2 + pas - 4:.2f}" width="20" height="5" fill="{{c}}"/>'
    legende_svg.append(texte_leg(x0 + 25, y2 + pas, "couvert arboré de plus de 50 %"))
    y3 = y2 + 2.5 * pas
    legende_svg.append(texte_leg(x0, y3, "Versants", gras=True))
    pastilles = {"jaune": [], "rose": []}
    for i, (texte, versant) in enumerate(LEGENDE_VERSANTS):
        y = y3 + (i + 1) * pas
        for encre in TEINTES[versant]:
            pastilles[encre].append(box(x0, y - 4, x0 + 20, y + 1))
        legende_svg.append(texte_leg(x0 + 25, y, texte))
    cartouche = box(*CARTOUCHE[:2], CARTOUCHE[0] + CARTOUCHE[2], CARTOUCHE[1] + CARTOUCHE[3])
    if not mer_mm.contains(cartouche):
        print("   (attention : le cartouche touche la terre en",
              [tuple(np.round(p.bounds)) for p in E._polygones(cartouche.difference(mer_mm)).geoms], ")")
    occupe.append(cartouche)

    # --- canevas, interrompu autour des noms ; tout s'arrête au cartouche
    lignes_geo = [LineString([(lon, lat) for lat in np.arange(20, 70.01, 0.1)])
                  for lon in range(-30, 40, PAS_GRATICULE)]
    lignes_geo += [LineString([(lon, lat) for lon in np.arange(-40, 50.01, 0.1)])
                   for lat in range(20, 70, PAS_GRATICULE)]
    graticule_mm = unary_union([geo_mm(l) for l in lignes_geo]).intersection(globe_mm)
    blanc_noms = unary_union(occupe).buffer(E.BLANC_AUTOUR_NOMS_MM)
    graticule_mm = graticule_mm.difference(blanc_noms)
    partage_versants = partage_versants.difference(blanc_noms)
    partage_bassins = partage_bassins.difference(blanc_noms)
    frontieres_mm = frontieres_mm.difference(blanc_noms)
    vide = cartouche.buffer(1.0)
    mer_mm = E._polygones(mer_mm.difference(vide))
    foret_imp = E._polygones(foret_imp.difference(vide))
    teintes_imp = {encre: unary_union([g.difference(vide)] + pastilles[encre]) for encre, g in teintes_imp.items()}
    rivieres_mm = [(t, g.difference(vide)) for t, g in rivieres_mm]
    graticule_mm = graticule_mm.difference(vide)

    # --- SVG
    def plein(g, c):
        return f'<path d="{E.d_poly(g)}" fill="{c}" fill-rule="evenodd" stroke="none"/>'

    def traits(g, ep, c, tirets=None, cap="round"):
        dash = f' stroke-dasharray="{tirets[0]} {tirets[1]}"' if tirets else ""
        return (f'<path d="{E.d_lignes(g)}" fill="none" stroke="{c}" stroke-width="{ep}"{dash} '
                f'stroke-linejoin="round" stroke-linecap="{cap}"/>')

    def legende_calque(texte, c):
        return (f'<text x="{PAGE_L / 2}" y="{PAGE_H - MARGE / 2 + 1.5}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="4" text-anchor="middle" fill="{c}">{texte}</text>')

    contenus = {
        "jaune": plein(teintes_imp["jaune"], "{c}"),
        "rose": plein(teintes_imp["rose"], "{c}"),
        "foret": plein(foret_imp, "{c}") + legende_foret,
        "relief": f'<path d="{trame_d}" fill="{{c}}" stroke="none"/>',
        "eaux": f'<defs>{"".join(defs)}</defs>' + plein(mer_mm, "{c}")
        + "".join(traits(g, t, "{c}") for t, g in rivieres_mm)
        + "".join(mers_svg) + "".join(noms_riv_svg) + "".join(legende_eaux_svg),
        "noir": traits(graticule_mm, TRAIT_GRATICULE_MM, "{c}")
        + traits(frontieres_mm, TRAIT_FRONTIERE_MM, "{c}")
        + traits(partage_bassins, TRAIT_BASSIN_MM, "{c}", TIRETS_BASSIN_MM)
        + traits(partage_versants, TRAIT_VERSANT_MM, "{c}", TIRETS_VERSANT_MM, cap="butt")
        + "".join(legende_svg),
    }
    noms = [("calque_1_jaune", "jaune", "1/6 — TERRE, VERSANTS MANCHE ET MÉDITERRANÉE (jaune paille)"),
            ("calque_2_rose", "rose", "2/6 — TERRE, VERSANTS ATLANTIQUE ET MÉDITERRANÉE (rose saumon)"),
            ("calque_3_foret", "foret", "3/6 — FORÊTS (vert)"),
            ("calque_4_relief", "relief", "4/6 — RELIEF (marron foncé, trame)"),
            ("calque_5_eaux", "eaux", "5/6 — MERS, LACS ET RIVIÈRES (bleu)"),
            ("calque_6_noir", "noir", "6/6 — PARTAGE DES EAUX, FRONTIÈRES, CANEVAS (noir)")]

    def ecrire(nom, contenu):
        chemin_svg = SORTIE / f"{nom}.svg"
        chemin_svg.write_text(contenu)
        subprocess.run(["inkscape", str(chemin_svg), "--export-type=pdf", "--export-text-to-path",
                        f"--export-filename={SORTIE / f'{nom}.pdf'}"], check=True, capture_output=True)
        print("  ", chemin_svg.name, "+ pdf")

    print("Écriture :")
    apercu = [("papier", "papier", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>')]
    for _, cle, lab in noms:
        apercu.append((cle, lab, contenus[cle].replace("{c}", COULEURS[cle])))
        if cle == "rose":   # superposition au jaune : couleurs multipliées
            dessus = teintes_imp["rose"].intersection(teintes_imp["jaune"])
            apercu.append(("melange", "jaune + rose (simulation)", plein(dessus, melange(COULEURS["jaune"], COULEURS["rose"]))))
    apercu.append(("reperes", "repères", E.reperes("#000")))
    ecrire("france_eaux_apercu", E.svg(apercu, "Les eaux de France — aperçu"))
    for nom, cle, lab in noms:
        c = [("fond", "fond", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>'),
             (cle, lab, contenus[cle].replace("{c}", "#000000")),
             ("reperes", "repères", E.reperes("#000") + legende_calque(lab, "#000"))]
        ecrire(nom, E.svg(c, f"Les eaux de France — {lab}"))


if __name__ == "__main__":
    main()
