"""Carte de l'Europe vue de l'espace, en 4 calques de sérigraphie.

Projection perspective verticale (vue satellite, « nsper ») : l'Europe remplit
la feuille et le haut montre l'horizon courbe du globe, au-delà du pôle Nord,
avec le papier blanc au-dessus. La glace (banquise médiane 2015-2024 et glaciers)
est en réserve : le blanc du papier, comme le ciel.

Ordre d'impression (la plus claire d'abord) :
  1. terre  (ocre clair, façon carte d'école) : toutes les terres ; déborde de
     0,3 mm sous la mer (trapping) ; neige des hauts sommets en réserve
  2. relief (marron foncé) : estompage du relief en trame de points, par-dessus l'ocre
  3. mer    (bleu) : mers, océans, grands lacs, et fleuves principaux imprimés
     par-dessus la terre
  4. noir : canevas, grands axes routiers, frontières, noms des pays, capitales et villes

Sorties dans sortie/ : un SVG et un PDF par calque (noir sur blanc),
plus un aperçu en couleur.
"""
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
import shapely.ops
import rasterio.features
import rasterio.transform
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import box, LineString, MultiPolygon, Polygon, Point
from shapely.ops import unary_union

ICI = Path(__file__).parent
DATA = ICI / "data"
SORTIE = ICI / "sortie"
SORTIE.mkdir(exist_ok=True)

# --- Point de vue ---------------------------------------------------------------
LAT_0, LON_0 = 41.5, 15.0        # point de la Terre à la verticale du satellite
ALTITUDE_KM = 3000               # altitude du point de vue : plus bas = courbure plus forte
R_TERRE = 6_371_000              # Terre sphérique (m)

# --- Paramètres d'impression (mm) -------------------------------------------
PAGE_L, PAGE_H = 700, 500        # format de la feuille (paysage)
MARGE = 25                       # marge autour de la carte (repères dedans)
LARGEUR_KM = 5200                # largeur couverte par la carte, au centre de la vue (km)
CIEL_MM = 15                     # blanc entre le haut de la carte et le sommet de l'horizon
AIRE_MIN_MM2 = 0.8               # îles et lacs plus petits : supprimés
SIMPLIF_MM = 0.05                # simplification des contours
FIN_MM = 0.2                     # filaments de terre ou d'eau plus fins : supprimés
TRAP_MM = 0.3                    # débord de la terre sous la mer
RANG_MAX = 7                     # fleuves gardés : rang Natural Earth ≤ RANG_MAX (plus = plus de rivières)
TRAITS_FLEUVES_MM = {3: 0.6, 5: 0.45, 7: 0.35, 99: 0.3}  # épaisseur selon le rang (≤ clé)

TRAIT_FRONTIERE_MM = 0.45

# Glace, en réserve (papier) sur la mer comme sur la terre : banquise présente au moins
# la moitié des mois de 2015 à 2024 (NSIDC), et glaciers terrestres (Natural Earth)
SEUIL_BANQUISE = 0.3             # part des mois où la glace doit être là
PIXEL_BANQUISE_M = 5000          # grille de calcul de la fréquence de la banquise
LAT_MIN_GLACIERS = 60            # glaciers gardés au nord de cette latitude (l'Arctique seulement)
LAT_MIN_BANQUISE = 66.5          # banquise gardée au nord du cercle polaire (pas la Baltique)
AIRE_MIN_GLACE_MM2 = 5           # plaques de glace isolées plus petites : supprimées

# Relief (ETOPO 2022) : estompage (lumière du nord-ouest) rendu en trame de points
# marron, plus dense dans les ombres ; neige en réserve au-dessus de NEIGE_M
PIXEL_RELIEF_M = 2000            # grille de calcul de l'estompage (azimutale équidistante)
LISSAGE_RELIEF_M = 5000          # généralisation du relief avant estompage (≈ une cellule de trame)
EXAGERATION = 8                  # exagération verticale du relief
SOLEIL = (315, 40)               # azimut et hauteur de la lumière (degrés)
GAIN_OMBRE = 1.5                 # couverture de trame = GAIN × intensité de l'ombre
LIGNES_CM = 18                   # linéature de la trame (lignes par cm)
ANGLE_TRAME = 45                 # angle de la trame (degrés)
COUVERTURE = (0.12, 0.70)        # couverture min (points plus petits : supprimés) et max
NEIGE_M = 2800                   # altitude de la neige (réserve blanche dans l'ocre et le relief)
PAS_GRILLE_MM = 0.25             # résolution de la grille de la carte (estompage, neige)
# Noms de pays, façon carte murale Vidal-Lablache : grotesque grasse étroite, en
# minuscules avec capitale, corps proportionnel à la taille du pays ; les noms
# débordent librement sur les voisins et la mer. Ils suivent le parallèle qui passe
# par leur point d'étiquette (même courbure que la Terre) ; si le nom couvre nettement
# mieux son pays le long de l'axe principal (Portugal, Italie…), il est écrit droit,
# incliné selon cet axe.
POLICE = "Helvetica Neue"        # Condensed Bold (font-stretch condensed, font-weight bold)
POLICE_FICHIER = ("/System/Library/Fonts/HelveticaNeue.ttc", "Condensed Bold")  # pour mesurer les noms
# Capitales, comme chez Vidal-Lablache : rond pointé, nom en italique souligné, placé
# autour du point là où il ne touche ni un nom de pays ni une autre capitale
CAPITALE_MM = 4.8                # corps des noms de capitales
CAPITALE_POLICE = ("/System/Library/Fonts/HelveticaNeue.ttc", "Bold Italic")
RAYON_CAPITALE_MM = 1.2          # rond du symbole (trait 0,35 mm, point central plein)
NOMS_CAPITALES = {"Noursoultan": "Astana"}   # noms à corriger (Astana a repris son nom en 2022)
# Villes : point plein et nom en romain, plus petits que les capitales ; placées par
# population décroissante, écartées s'il n'y a pas la place pour leur nom
VILLE_POP_MIN = 200_000          # population de l'agglomération (POP_MAX de Natural Earth)
VILLE_POP_RESERVEE = 1_000_000   # au-delà, le point est réservé avant les noms de pays
VILLE_MM = (3.0, 3.6)            # corps des noms de villes : moins / plus de VILLE_POP_RESERVEE
VILLE_POLICE = ("/System/Library/Fonts/HelveticaNeue.ttc", "Medium")
RAYON_VILLE_MM = (0.5, 0.65)     # point plein : moins / plus de VILLE_POP_RESERVEE
# Grands axes routiers (Natural Earth, rang ≤ ROUTE_RANG_MAX), en noir fin, sur la terre,
# interrompus autour des noms et des points de villes
ROUTE_RANG_MAX = 4
TRAIT_ROUTE_MM = 0.3
# Canevas : parallèles et méridiens tous les PAS_GRATICULE degrés, en trait fin ; seuls
# les méridiens multiples de 30° montent jusqu'au pôle (sinon ils s'arrêtent à 80° N).
# Les lignes s'interrompent autour des noms ; les degrés sont inscrits au bord de la carte.
PAS_GRATICULE = 10
TRAIT_GRATICULE_MM = 0.2
BLANC_AUTOUR_NOMS_MM = 0.6       # interruption du canevas autour des noms
DEGRES_MM = 2.8                  # corps des degrés en bordure
TAILLE_NOM_MM = (4.0, 30.0)      # corps des noms de pays : min, max
FACTEUR_TAILLE = 0.2             # corps = FACTEUR × √(surface visible du pays en mm²)
REDUCTION_MAX = 0.6              # en cas de collision, on peut réduire jusqu'à 60 % du corps
GAIN_INCLINE = 0.1               # on incline si le nom couvre son pays 10 points de % mieux
AIRE_MIN_NOM_MM2 = 60            # pays plus petits sur la carte : pas de nom (Luxembourg, Malte…)
# noms trop longs pour leur pays : coupés sur deux lignes
NOMS = {"Bosnia and Herzegovina": "Bosnie-\nHerzégovine", "North Macedonia": "Macédoine\ndu Nord"}
NOMS_EXCLUS = {"Kuwait", "Uzbekistan", "Turkmenistan", "Kyrgyzstan"}   # pays qu'on ne nomme pas (écrasés contre le bord)
# positions (lon, lat[, angle en degrés pour forcer un nom droit]) qui remplacent le point
# d'étiquette de Natural Earth (Afrique du Nord et Kazakhstan : hors de la partie visible ;
# Royaume-Uni : tombe en mer ; Croatie : pays en croissant ; Caucase : trop serré ;
# Monténégro : en mer ; Espagne : décalée pour laisser le Portugal à la verticale ;
# Moldavie : inclinée le long du pays ; Allemagne : au nord, entre les grandes villes)
POSITIONS = {"Morocco": (-5.2, 34.2), "Algeria": (3.5, 35.4), "Tunisia": (9.4, 34.6),
             "Kazakhstan": (52.0, 48.8), "United Kingdom": (-1.6, 52.7), "Croatia": (17.6, 45.42),
             "Azerbaijan": (47.9, 40.4), "Armenia": (44.75, 40.1), "Montenegro": (18.3, 42.0, 0),
             "Spain": (-2.6, 39.9), "Moldova": (28.45, 47.05, 61),
             "Germany": (10.2, 52.0)}

COULEURS = {"terre": "#e8d08c", "relief": "#6b4a2b", "mer": "#3d6f9e", "noir": "#1a1a1a"}

SPHERE = f"+R={R_TERRE} +units=m +no_defs"
AEQD = f"+proj=aeqd +lat_0={LAT_0} +lon_0={LON_0} {SPHERE}"
NSPER = f"+proj=nsper +h={ALTITUDE_KM * 1000} +lat_0={LAT_0} +lon_0={LON_0} {SPHERE}"
GEO = f"+proj=longlat {SPHERE}"


def projeter(geom, tr):
    return shapely.transform(geom, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


# --- Géométrie ------------------------------------------------------------------
def calotte_visible():
    """Calotte visible depuis le satellite, sous forme de disque en azimutale
    équidistante (où elle est exactement un cercle). Rayon un poil réduit pour
    rester à l'écart de l'horizon, où la perspective est singulière."""
    theta = np.arccos(R_TERRE / (R_TERRE + ALTITUDE_KM * 1000))
    return Point(0, 0).buffer(R_TERRE * theta * 0.9995, quad_segs=512), np.degrees(theta)


def charger(theta_deg):
    terres = gpd.read_file(DATA / "ne_10m_land" / "ne_10m_land.shp")
    lacs = gpd.read_file(DATA / "ne_10m_lakes" / "ne_10m_lakes.shp")
    # la calotte contient le pôle Nord : on ne garde que ce qui est au nord de sa
    # limite sud, ce qui écarte l'Antarctique et ses bords artificiels
    zone = box(-180, LAT_0 - theta_deg - 1, 180, 90)
    terres = unary_union(terres.geometry.buffer(0).intersection(zone))
    lacs = unary_union(lacs[lacs.intersects(zone)].geometry.buffer(0))
    fleuves = gpd.read_file(DATA / "ne_10m_rivers_lake_centerlines" / "ne_10m_rivers_lake_centerlines.shp")
    fleuves = fleuves[(fleuves.scalerank <= RANG_MAX) & (fleuves.featurecla != "Canal")
                      & fleuves.intersects(zone)]
    fleuves = [(rang, unary_union(list(g.geometry.intersection(zone))))
               for rang, g in fleuves.groupby("scalerank")]

    # frontières internationalement reconnues ; on écarte les lignes internes à
    # Chypre et les lignes « disputées » russo-ukrainiennes (Crimée, Kertch)
    lignes = gpd.read_file(DATA / "ne_10m_admin_0_boundary_lines_land" / "ne_10m_admin_0_boundary_lines_land.shp")
    crimee = lignes.ADM0_LEFT.isin(["Russia", "Ukraine"]) | lignes.ADM0_LEFT.isna()
    lignes = lignes[(lignes.FEATURECLA == "International boundary (verify)")
                    | (lignes.FEATURECLA.isin(["Disputed (please verify)", "Indefinite (please verify)"]) & ~crimee)]
    frontieres = unary_union(list(lignes.geometry.intersection(zone)))

    villes = gpd.read_file(DATA / "ne_10m_populated_places" / "ne_10m_populated_places.shp")
    capitales = villes[(villes.FEATURECLA == "Admin-0 capital") & villes.intersects(zone)]
    routes = gpd.read_file(DATA / "ne_10m_roads" / "ne_10m_roads.shp")
    routes = routes[(routes.featurecla == "Road") & (routes.scalerank <= ROUTE_RANG_MAX) & routes.intersects(zone)]
    routes = unary_union(list(routes.geometry.intersection(zone)))
    grandes_villes = villes[(villes.FEATURECLA != "Admin-0 capital") & (villes.POP_MAX >= VILLE_POP_MIN)
                            & villes.intersects(zone)].sort_values("POP_MAX", ascending=False)

    glaciers = gpd.read_file(DATA / "ne_10m_glaciated_areas" / "ne_10m_glaciated_areas.shp")
    arctique = box(-180, LAT_MIN_GLACIERS, 180, 90)   # pas les Alpes ni le Caucase
    glaciers = unary_union(list(glaciers[glaciers.intersects(arctique)].geometry.buffer(0).intersection(arctique)))

    pays = gpd.read_file(DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp")
    pays = pays[pays.TYPE.isin(["Sovereign country", "Country", "Disputed", "Sovereignty"])
                & pays.intersects(zone)].copy()
    pays["geometry"] = pays.geometry.buffer(0).intersection(zone)
    return terres, lacs, fleuves, frontieres, pays, capitales, grandes_villes, glaciers, routes


def banquise_mediane():
    """Banquise présente au moins SEUIL_BANQUISE des mois (polygones NSIDC), dans le
    système polaire stéréographique du NSIDC. Renvoie (géométrie, crs)."""
    fichiers = sorted((DATA / "banquise").glob("*/*.shp"))
    crs = gpd.read_file(fichiers[0]).crs
    x0, y0, x1, y1 = -4_000_000, -4_000_000, 4_000_000, 4_000_000
    larg = int((x1 - x0) / PIXEL_BANQUISE_M)
    transform = rasterio.transform.from_bounds(x0, y0, x1, y1, larg, larg)
    compte = np.zeros((larg, larg), dtype="uint16")
    for f in fichiers:
        g = gpd.read_file(f).to_crs(crs).geometry
        compte += rasterio.features.rasterize(((geom, 1) for geom in g if geom is not None),
                                              out_shape=compte.shape, transform=transform, dtype="uint16")
    freq = ndimage.gaussian_filter(compte / len(fichiers), 1.0)
    # suréchantillonnage x2 avant vectorisation : pas de marches d'escalier
    fin = ndimage.zoom(freq, 2, order=1) >= SEUIL_BANQUISE
    polys = [shapely.geometry.shape(g) for g, v in rasterio.features.shapes(
        fin.astype("uint8"), mask=fin, transform=transform * transform.scale(0.5)) if v == 1]
    print(f"   banquise : {len(fichiers)} mois, seuil {SEUIL_BANQUISE:.0%}")
    # au nord du cercle polaire : en stéréographique polaire, un parallèle est un cercle
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(0, LAT_MIN_BANQUISE)
    cercle = Point(0, 0).buffer(np.hypot(x, y), quad_segs=256)
    return unary_union(polys).simplify(PIXEL_BANQUISE_M / 4).intersection(cercle), crs


def grille_carte(ech, top_m):
    """Grille raster couvrant la carte : (forme, transform nsper, transform mm)."""
    cadre_l, cadre_h = PAGE_L - 2 * MARGE, PAGE_H - 2 * MARGE
    forme = (int(cadre_h / PAS_GRILLE_MM), int(cadre_l / PAS_GRILLE_MM))
    t_nsper = rasterio.transform.from_origin(-cadre_l / 2 * ech, top_m, PAS_GRILLE_MM * ech, PAS_GRILLE_MM * ech)
    t_mm = rasterio.Affine(PAS_GRILLE_MM, 0, MARGE, 0, PAS_GRILLE_MM, MARGE)
    return forme, t_nsper, t_mm


def relief_carte(rayon_calotte, forme, t_nsper):
    """Altitude (m) et intensité de l'ombre (0-1) sur la grille de la carte.

    L'estompage est calculé dans l'azimutale équidistante (peu déformée), puis
    reporté dans la perspective : la lumière vient bien du nord-ouest partout."""
    from rasterio.warp import reproject, Resampling
    with rasterio.open(DATA / "etopo_europe.tif") as src:
        z_src, t_src, crs_src = src.read(1).astype("float32"), src.transform, src.crs
    z_src[z_src == -32768] = np.nan

    n = int(2 * rayon_calotte / PIXEL_RELIEF_M)
    t_aeqd = rasterio.transform.from_origin(-rayon_calotte, rayon_calotte, PIXEL_RELIEF_M, PIXEL_RELIEF_M)
    z = np.full((n, n), np.nan, dtype="float32")
    reproject(z_src, z, src_transform=t_src, src_crs=crs_src, src_nodata=np.nan,
              dst_transform=t_aeqd, dst_crs=AEQD, dst_nodata=np.nan, resampling=Resampling.bilinear)
    z0 = ndimage.gaussian_filter(np.nan_to_num(np.maximum(z, 0)), LISSAGE_RELIEF_M / PIXEL_RELIEF_M) * EXAGERATION
    dzdy, dzdx = np.gradient(z0, PIXEL_RELIEF_M)   # lignes vers le sud : dzdy pointe au sud
    pente = np.arctan(np.hypot(dzdx, dzdy))
    az, haut = np.radians(SOLEIL[0]), np.radians(SOLEIL[1])
    zen = np.pi / 2 - haut
    # direction de descente : (-dzdx, +dzdy) en (est, nord) ; angle compté depuis le nord
    descente = np.arctan2(-dzdx, dzdy)
    eclairage = np.cos(zen) * np.cos(pente) + np.sin(zen) * np.sin(pente) * np.cos(az - descente)
    ombre = np.clip((np.cos(zen) - eclairage) / np.cos(zen), 0, 1).astype("float32")

    ombre_carte = np.zeros(forme, dtype="float32")
    reproject(ombre, ombre_carte, src_transform=t_aeqd, src_crs=AEQD,
              dst_transform=t_nsper, dst_crs=NSPER, dst_nodata=0, resampling=Resampling.bilinear)
    z_carte = np.zeros(forme, dtype="float32")
    reproject(np.nan_to_num(z_src), z_carte, src_transform=t_src, src_crs=crs_src,
              dst_transform=t_nsper, dst_crs=NSPER, dst_nodata=0, resampling=Resampling.bilinear)
    return z_carte, ombre_carte


def vectoriser(masque, t_mm):
    """Masque raster -> polygones lissés (en mm), sans marches d'escalier."""
    k = 4
    fin = ndimage.zoom(ndimage.gaussian_filter(masque.astype("float32"), 1.0), k, order=1) >= 0.5
    polys = [shapely.geometry.shape(g) for g, v in rasterio.features.shapes(
        fin.astype("uint8"), mask=fin, transform=t_mm * t_mm.scale(1 / k)) if v == 1]
    return unary_union(polys).simplify(SIMPLIF_MM).buffer(0)


def trame(intensite, t_mm, zone):
    """Trame de points vectoriels : un point par cellule, de surface = couverture."""
    cellule = 10 / LIGNES_CM
    x0, y0, x1, y1 = zone.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    demi = np.hypot(x1 - x0, y1 - y0) / 2
    u = np.arange(-demi, demi, cellule)
    uu, vv = np.meshgrid(u, u)
    a = np.radians(ANGLE_TRAME)
    xs = cx + uu * np.cos(a) - vv * np.sin(a)
    ys = cy + uu * np.sin(a) + vv * np.cos(a)
    ok = (xs > x0) & (xs < x1) & (ys > y0) & (ys < y1)
    xs, ys = xs[ok], ys[ok]
    # couverture lue sur la grille (bilinéaire), zone lue sur un masque fin
    inv = ~t_mm
    col, lig = inv * (xs, ys)
    c = ndimage.map_coordinates(intensite, [lig - 0.5, col - 0.5], order=1, mode="constant") * GAIN_OMBRE
    garde = c >= COUVERTURE[0]
    xs, ys, c = xs[garde], ys[garde], np.minimum(c[garde], COUVERTURE[1])
    pas = 0.1
    forme = (int((PAGE_H - 2 * MARGE) / pas), int((PAGE_L - 2 * MARGE) / pas))
    t_fin = rasterio.Affine(pas, 0, MARGE, 0, pas, MARGE)
    masque = rasterio.features.rasterize([(zone.buffer(-0.15), 1)], out_shape=forme, transform=t_fin, dtype="uint8")
    col, lig = (~t_fin) * (xs, ys)
    dedans = masque[np.clip(lig.astype(int), 0, forme[0] - 1), np.clip(col.astype(int), 0, forme[1] - 1)] == 1
    xs, ys, c = xs[dedans], ys[dedans], c[dedans]
    r = cellule * np.sqrt(c / np.pi)
    print(f"   trame : {len(xs):,} points ({LIGNES_CM} lignes/cm, {ANGLE_TRAME}°)")
    return "".join(f"M{x - rr:.2f},{y:.2f}a{rr:.2f},{rr:.2f} 0 1,0 {2 * rr:.2f},0a{rr:.2f},{rr:.2f} 0 1,0 {-2 * rr:.2f},0Z"
                   for x, y, rr in zip(xs, ys, r))


def epaisseur(rang):
    return next(e for r, e in sorted(TRAITS_FLEUVES_MM.items()) if rang <= r)


def mise_en_page(horizon):
    """Renvoie la fonction nsper -> mm et le cadre de la carte (mm)."""
    cadre_l, cadre_h = PAGE_L - 2 * MARGE, PAGE_H - 2 * MARGE
    ech = LARGEUR_KM * 1000 / cadre_l           # m par mm
    y_haut = horizon.bounds[3]                   # sommet de l'horizon (m)
    top_m = y_haut + CIEL_MM * ech               # haut de la carte en coordonnées nsper

    def vers_mm(g):
        g = shapely.affinity.translate(g, cadre_l / 2 * ech, -top_m)
        g = shapely.affinity.scale(g, 1 / ech, -1 / ech, origin=(0, 0))
        return shapely.affinity.translate(g, MARGE, MARGE)

    return vers_mm, ech, top_m


def nettoyer(g, aire_min):
    """Supprime les polygones et les trous plus petits que aire_min."""
    polys = []
    for p in _polygones(g).geoms:
        if p.area < aire_min:
            continue
        trous = [t for t in p.interiors if Polygon(t).area >= aire_min]
        polys.append(Polygon(p.exterior, trous))
    return MultiPolygon(polys)


def _polygones(g):
    if g.is_empty:
        return MultiPolygon()
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    if isinstance(g, MultiPolygon):
        return g
    return MultiPolygon([p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)])


def mesureur(fichier, style):
    """Renvoie une fonction : texte -> largeur en corps (em), d'après la police réelle."""
    from fontTools.ttLib import TTCollection, TTFont
    polices = TTCollection(fichier).fonts if fichier.endswith(".ttc") else [TTFont(fichier)]
    police = next(f for f in polices if f["name"].getDebugName(2) == style)
    cmap, hmtx = police.getBestCmap(), police["hmtx"]
    upm = police["head"].unitsPerEm
    return lambda texte: sum(hmtx[cmap.get(ord(ch), ".notdef")][0] for ch in texte) / upm


# --- SVG ----------------------------------------------------------------------
def d_poly(g):
    parts = []
    for p in _polygones(g).geoms:
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            parts.append("M" + " ".join(f"{x:.2f},{y:.2f}" for x, y in c[:-1]) + "Z")
    return "".join(parts)


def d_lignes(g):
    parts = []
    for l in getattr(g, "geoms", [g]):
        if isinstance(l, LineString):
            c = np.asarray(l.coords)
            if len(c) > 1:
                parts.append("M" + " ".join(f"{x:.2f},{y:.2f}" for x, y in c))
        elif hasattr(l, "geoms"):
            parts.append(d_lignes(l))
    return "".join(parts)


def reperes(coul):
    """Croix de repérage cerclées dans les 4 coins de la marge."""
    out = []
    r, m = 4, MARGE / 2
    for x, y in [(m, m), (PAGE_L - m, m), (m, PAGE_H - m), (PAGE_L - m, PAGE_H - m)]:
        out.append(f'<circle cx="{x}" cy="{y}" r="{r * 0.6}" fill="none" stroke="{coul}" stroke-width="0.3"/>'
                   f'<path d="M{x - r},{y}H{x + r}M{x},{y - r}V{y + r}" stroke="{coul}" stroke-width="0.3"/>')
    return "".join(out)


def svg(calques, titre):
    """calques : liste de (id, label, contenu_svg)."""
    corps = "".join(
        f'<g inkscape:groupmode="layer" id="{i}" inkscape:label="{lab}">{c}</g>'
        for i, lab, c in calques)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{PAGE_L}mm" height="{PAGE_H}mm" viewBox="0 0 {PAGE_L} {PAGE_H}">'
            f'<title>{titre}</title>{corps}</svg>\n')


def ecrire(nom, contenu):
    chemin_svg = SORTIE / f"{nom}.svg"
    chemin_svg.write_text(contenu)
    subprocess.run(["inkscape", str(chemin_svg), "--export-type=pdf", "--export-text-to-path",
                    f"--export-filename={SORTIE / f'{nom}.pdf'}"],
                   check=True, capture_output=True)
    print("  ", chemin_svg.name, "+ pdf")


# --- Principal ----------------------------------------------------------------
def main():
    calotte, theta = calotte_visible()
    print(f"Horizon à {theta:.1f}° du centre de la vue")
    terres, lacs, fleuves, frontieres, pays, capitales, grandes_villes, glaciers, routes = charger(theta)
    banquise, crs_banquise = banquise_mediane()

    geo_vers_aeqd = Transformer.from_crs(GEO, AEQD, always_xy=True)
    aeqd_vers_nsper = Transformer.from_crs(AEQD, NSPER, always_xy=True)

    print("Projection…")
    terres = projeter(terres, geo_vers_aeqd).buffer(0).intersection(calotte)
    lacs = projeter(lacs, geo_vers_aeqd).buffer(0).intersection(calotte)
    terres = terres.difference(lacs)
    # en azimutale équidistante, les contours de NE 10m sont assez denses ;
    # on densifie le bord de la calotte avant de passer en perspective
    horizon = projeter(calotte.segmentize(R_TERRE * 0.002), aeqd_vers_nsper)
    terres = projeter(terres, aeqd_vers_nsper).buffer(0)
    fleuves = [(rang, projeter(projeter(f, geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper))
               for rang, f in fleuves]
    frontieres = projeter(projeter(frontieres, geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper)
    routes = projeter(projeter(routes, geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper)
    banquise_vers_aeqd = Transformer.from_crs(crs_banquise, AEQD, always_xy=True)
    banquise = projeter(projeter(banquise.segmentize(20_000), banquise_vers_aeqd).buffer(0)
                        .intersection(calotte), aeqd_vers_nsper).buffer(0)
    glaciers = projeter(projeter(glaciers, geo_vers_aeqd).buffer(0).intersection(calotte),
                        aeqd_vers_nsper).buffer(0)
    capitales_nsper = []   # (nom, code pays, point nsper)
    for _, v in capitales.iterrows():
        pt = projeter(v.geometry, geo_vers_aeqd)
        if calotte.contains(pt):
            capitales_nsper.append((NOMS_CAPITALES.get(v.NAME_FR, v.NAME_FR), v.ADM0_A3,
                                    projeter(pt, aeqd_vers_nsper)))
    villes_nsper = []      # (nom, population, point nsper), par population décroissante
    for _, v in grandes_villes.iterrows():
        pt = projeter(v.geometry, geo_vers_aeqd)
        if calotte.contains(pt):
            villes_nsper.append((v.NAME_FR, v.POP_MAX, projeter(pt, aeqd_vers_nsper)))
    etiquettes = []   # (nom, code pays, lon, lat, angle forcé ou None, surface nsper)
    for _, p in pays[~pays.ADMIN.isin(NOMS_EXCLUS)].iterrows():
        lon, lat, *angle = POSITIONS.get(p.ADMIN, (p.LABEL_X, p.LABEL_Y))
        pt = projeter(Point(lon, lat), geo_vers_aeqd)
        if not calotte.contains(pt):
            continue
        surface = projeter(projeter(p.geometry, geo_vers_aeqd).buffer(0).intersection(calotte),
                           aeqd_vers_nsper).buffer(0)
        nom = NOMS.get(p.ADMIN, p.NAME_FR)
        etiquettes.append((nom, p.ADM0_A3, lon, lat, angle[0] if angle else None, surface))

    vers_mm, ech, top_m = mise_en_page(horizon)
    print(f"Au centre de la vue : 1 mm = {ech / 1000:.1f} km  (1:{ech * 1000:,.0f})")

    cadre = box(MARGE, MARGE, PAGE_L - MARGE, PAGE_H - MARGE)
    globe_mm = vers_mm(horizon).intersection(cadre)
    # simplification avant la découpe, pour garder un horizon parfaitement rond ;
    # puis ouverture (filaments de terre) et fermeture (filaments d'eau), surtout
    # utiles près de l'horizon où la perspective écrase tout
    r = FIN_MM / 2
    terres_mm = vers_mm(terres).simplify(SIMPLIF_MM).buffer(0)
    terres_mm = terres_mm.buffer(-r).buffer(r).buffer(r).buffer(-r)
    # le nettoyage rogne un peu la terre au ras de l'horizon : on l'y ramène
    bande = vers_mm(horizon).difference(vers_mm(horizon).buffer(-3 * r))
    terres_mm = terres_mm.union(terres_mm.buffer(3 * r).intersection(bande))
    terres_mm = nettoyer(terres_mm.intersection(globe_mm), AIRE_MIN_MM2)

    # la mer, sans les flaques trop petites ; c'est la terre (plus claire) qui
    # déborde dessous
    mer_mm = nettoyer(globe_mm.difference(terres_mm), AIRE_MIN_MM2)
    terres_mm = _polygones(globe_mm.difference(mer_mm).buffer(TRAP_MM).intersection(globe_mm))

    # fleuves : traits pleins, sur la terre seulement ; ils s'impriment
    # par-dessus l'ocre (pas de réserve, trop fins pour un débord)
    terre_nette = globe_mm.difference(mer_mm)
    traits = [vers_mm(f).intersection(terre_nette).buffer(epaisseur(rang) / 2, quad_segs=4)
              for rang, f in fleuves]
    fleuves_mm = unary_union(traits)
    mer_mm = _polygones(unary_union([mer_mm, fleuves_mm]))
    print(f"   terres : {len(terres_mm.geoms)} polygones, fleuves : rangs ≤ {RANG_MAX}")

    # glace : réserve dans les deux encres (le papier), sans miettes ni filaments
    # (banquise sur la mer seulement, glaciers sur la terre seulement)
    mer_seule = globe_mm.difference(terre_nette)
    glace_mm = unary_union([vers_mm(banquise).intersection(mer_seule),
                            vers_mm(glaciers).intersection(terre_nette)])
    glace_mm = glace_mm.simplify(SIMPLIF_MM).buffer(0)
    glace_mm = glace_mm.buffer(-r).buffer(r).buffer(r).buffer(-r)
    glace_mm = nettoyer(glace_mm.intersection(globe_mm), AIRE_MIN_GLACE_MM2)
    terres_mm = nettoyer(terres_mm.difference(glace_mm), AIRE_MIN_MM2)
    mer_mm = nettoyer(mer_mm.difference(glace_mm), AIRE_MIN_MM2)
    print(f"   glace : {glace_mm.area:,.0f} mm² en réserve")

    # relief : neige (réserve dans l'ocre) et trame d'estompage sur les terres restantes
    print("Relief…")
    forme, t_nsper, t_mm = grille_carte(ech, top_m)
    z_carte, ombre_carte = relief_carte(calotte.bounds[2], forme, t_nsper)
    neige_mm = vectoriser(z_carte >= NEIGE_M, t_mm).intersection(terre_nette).difference(glace_mm)
    neige_mm = nettoyer(neige_mm, AIRE_MIN_MM2)
    terres_mm = nettoyer(terres_mm.difference(neige_mm), AIRE_MIN_MM2)
    print(f"   neige au-dessus de {NEIGE_M} m : {len(neige_mm.geoms)} taches, {neige_mm.area:.0f} mm²")
    zone_relief = terre_nette.difference(glace_mm).difference(neige_mm.buffer(0.2))
    trame_d = trame(ombre_carte, t_mm, zone_relief)

    frontieres_mm = vers_mm(frontieres).intersection(globe_mm)
    rayon_calotte = calotte.bounds[2]
    mesure = mesureur(*POLICE_FICHIER)

    def parallele_mm(lon, lat):
        """Le parallèle `lat` autour de `lon`, projeté en mm, d'ouest en est."""
        lons = np.arange(lon - 60, lon + 60, 0.05)
        x, y = geo_vers_aeqd.transform(lons, np.full_like(lons, lat))
        ok = np.hypot(x, y) < rayon_calotte
        xs, ys = aeqd_vers_nsper.transform(x[ok], y[ok])
        return vers_mm(LineString(np.column_stack([xs, ys])))

    def axe_principal(surface_mm):
        """Angle (degrés, sens SVG) du grand côté du rectangle minimal du plus grand morceau."""
        morceau = max(_polygones(surface_mm).geoms, key=lambda g: g.area)
        c = np.asarray(morceau.minimum_rotated_rectangle.exterior.coords)
        cotes = np.diff(c[:3], axis=0)
        dx, dy = max(cotes, key=lambda v: np.hypot(*v))
        a = np.degrees(np.arctan2(dy, dx))
        a = (a + 90) % 180 - 90           # dans [-90, 90[ : lisible de gauche à droite
        return -90.0 if a == -90 or a > 80 else a, morceau

    def largeur(lignes, t):
        return max(mesure(l) for l in lignes) * t

    def decalage(i, lignes, t):
        # décalage du centre de la ligne i vers le bas (offset_curve > 0 = vers le bas en mm)
        return (i - (len(lignes) - 1) / 2) * t * 1.05

    def emprise_courbe(ligne, s0, lignes, t):
        """Emprise d'un nom centré à l'abscisse s0 le long du parallèle, ou None s'il déborde."""
        L = largeur(lignes, t)
        if s0 - L / 2 < 0 or s0 + L / 2 > ligne.length:
            return None
        seg = shapely.ops.substring(ligne, s0 - L / 2, s0 + L / 2)
        return unary_union([seg.offset_curve(decalage(i, lignes, t)).buffer(t * 0.42, cap_style="flat")
                            for i in range(len(lignes))])

    def emprise_droite(x, y, lignes, t, angle):
        L, H = largeur(lignes, t), (len(lignes) - 1) * t * 1.05 + t * 0.84
        return shapely.affinity.rotate(box(x - L / 2, y - H / 2, x + L / 2, y + H / 2), angle, origin=(x, y))

    candidats = []
    for nom, code, lon, lat, angle, surface in etiquettes:
        surface_mm = vers_mm(surface).intersection(globe_mm)
        if surface_mm.area >= AIRE_MIN_NOM_MM2:
            candidats.append((surface_mm.area, nom, code, lon, lat, angle, surface_mm))

    # capitales des pays nommés (pas les micro-États) : on réserve d'abord leur
    # symbole, pour que les noms de pays ne passent pas dessus
    codes = {c[2] for c in candidats}
    capitales_mm = [(nom, vers_mm(pt)) for nom, code, pt in capitales_nsper
                    if code in codes and globe_mm.contains(vers_mm(pt))]
    occupe = [pt.buffer(RAYON_CAPITALE_MM + 0.5) for _, pt in capitales_mm]
    # (de même pour les points des plus grandes villes)
    villes_mm = [(nom, pop, vers_mm(pt)) for nom, pop, pt in villes_nsper
                 if globe_mm.contains(vers_mm(pt)) and terre_nette.contains(vers_mm(pt))]
    occupe += [pt.buffer(RAYON_VILLE_MM[1] + 0.4) for _, pop, pt in villes_mm if pop >= VILLE_POP_RESERVEE]
    n_reserves = len(occupe)

    defs, noms_svg = [], []
    # les grands pays d'abord : ils gardent leur place, les petits s'adaptent
    for k, (aire, nom, code, lon, lat, angle, surface_mm) in enumerate(sorted(candidats, key=lambda c: -c[0])):
        lignes = nom.split("\n")
        t0 = float(np.clip(FACTEUR_TAILLE * np.sqrt(aire), *TAILLE_NOM_MM))
        tailles = np.arange(t0, max(TAILLE_NOM_MM[0], REDUCTION_MAX * t0) - 1e-9, -0.5)
        pays_large = surface_mm.buffer(1.0)

        def couverture(e):
            """Part de l'emprise du nom qui tombe dans son pays."""
            return e.intersection(pays_large).area / e.area

        def libre(e):
            return (e is not None and globe_mm.contains(e)
                    and not any(e.intersects(o) for o in occupe))

        reel = vers_mm(projeter(projeter(Point(lon, lat), geo_vers_aeqd), aeqd_vers_nsper))
        if not globe_mm.contains(reel):
            continue   # point d'étiquette hors de la carte (Iran, Ouzbékistan…)
        ligne = parallele_mm(lon, lat)
        pt = ligne.interpolate(ligne.project(reel))
        s0 = ligne.project(pt)
        a, morceau = axe_principal(surface_mm) if angle is None else (angle, surface_mm)
        c = morceau.centroid if morceau.contains(morceau.centroid) else pt
        cx, cy = (pt.x, pt.y) if angle is not None else (c.x, c.y)

        # courbe ou incliné : celui qui couvre le mieux son pays, au corps de départ
        e_c = emprise_courbe(ligne, s0, lignes, t0) if angle is None else None
        e_d = emprise_droite(cx, cy, lignes, t0, a)
        incline = angle is not None or e_c is None or (
            abs(a) > 10 and couverture(e_d) > couverture(e_c) + GAIN_INCLINE)
        ordre = ["droit", "courbe"] if incline else ["courbe", "droit"]
        if angle is not None:
            ordre = ["droit"]

        # en cas de collision : d'abord un léger décalage perpendiculaire, puis on réduit
        choix = None
        sin_a, cos_a = np.sin(np.radians(a)), np.cos(np.radians(a))
        # (l'orientation préférée est épuisée, décalages et réductions compris, avant l'autre)
        for mode in ordre:
            for t in tailles:
                for d in (0, 0.6 * t, -0.6 * t, 1.2 * t, -1.2 * t):
                    if mode == "courbe":
                        ligne_d = ligne.offset_curve(d) if d else ligne
                        e = emprise_courbe(ligne_d, ligne_d.project(pt), lignes, t)
                    else:
                        e = emprise_droite(cx - sin_a * d, cy + cos_a * d, lignes, t, a)
                    if libre(e):
                        choix = mode
                        break
                if choix:
                    break
            if choix:
                break
        if not choix:
            print(f"   (pas de place pour {nom!r})")
            continue
        occupe.append(e)
        if choix == "courbe":
            ligne = ligne_d
            pt = ligne.interpolate(ligne.project(pt))
        else:
            cx, cy = cx - sin_a * d, cy + cos_a * d

        style = (f'font-family="{POLICE}" font-weight="bold" font-stretch="condensed" '
                 f'font-size="{t:.2f}" text-anchor="middle" fill="{{c}}"')
        if choix == "courbe":
            for i, l in enumerate(lignes):
                # ligne de base sous le centre des capitales
                base = ligne.offset_curve(decalage(i, lignes, t) + 0.36 * t)
                ident = f"nom{k}_{i}"
                defs.append(f'<path id="{ident}" d="{d_lignes(base)}"/>')
                noms_svg.append(f'<text {style}><textPath xlink:href="#{ident}" '
                                f'startOffset="{base.project(pt):.2f}">{l}</textPath></text>')
        else:
            y0 = cy - (len(lignes) - 1) * t * 0.525 + t * 0.36
            tspans = "".join(f'<tspan x="{cx:.2f}" y="{y0 + i * t * 1.05:.2f}">{l}</tspan>'
                             for i, l in enumerate(lignes))
            noms_svg.append(f'<text {style} transform="rotate({a:.1f} {cx:.2f} {cy:.2f})">{tspans}</text>')
            if abs(a) > 1:
                print(f"   (nom incliné à {a:.0f}° : {nom!r})")
    noms_svg.insert(0, f'<defs>{"".join(defs)}</defs>')
    print(f"   {len(occupe) - n_reserves} noms de pays")

    def placer_nom(x, y, largeur_em, tailles, g):
        """Cherche une place libre pour un nom autour du point (est, ouest, diagonales,
        nord, sud). Renvoie (gauche, centre, corps, largeur) ou None ; occupe la place."""
        for t in tailles:
            L, h = largeur_em * t, 0.5 * t    # h : demi-hauteur de l'emprise (avec soulignement)
            for gauche, centre in [(x + g, y), (x - g - L, y),
                                   (x + 0.7 * g, y - 0.7 * g - h), (x + 0.7 * g, y + 0.7 * g + h),
                                   (x - 0.7 * g - L, y - 0.7 * g - h), (x - 0.7 * g - L, y + 0.7 * g + h),
                                   (x - L / 2, y - g - h), (x - L / 2, y + g + h)]:
                e = box(gauche, centre - h, gauche + L, centre + h)
                if globe_mm.contains(e) and not any(e.intersects(o) for o in occupe):
                    occupe.append(e)
                    return gauche, centre, t, L
        return None

    # capitales : symbole, puis nom autour du point
    mesure_cap = mesureur(*CAPITALE_POLICE)
    capitales_svg = []
    for nom, pt in capitales_mm:
        x, y = pt.x, pt.y
        capitales_svg.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{RAYON_CAPITALE_MM}" fill="none" stroke="{{c}}" stroke-width="0.35"/>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{0.4 * RAYON_CAPITALE_MM:.2f}" fill="{{c}}"/>')
        place = placer_nom(x, y, mesure_cap(nom), (CAPITALE_MM, 0.85 * CAPITALE_MM), RAYON_CAPITALE_MM + 0.8)
        if not place:
            print(f"   (pas de place pour la capitale {nom!r})")
            continue
        gauche, centre, t, L = place
        base = centre + 0.25 * t
        capitales_svg.append(
            f'<text x="{gauche:.2f}" y="{base:.2f}" font-family="{POLICE}" font-weight="bold" '
            f'font-style="italic" font-size="{t:.2f}" fill="{{c}}">{nom}</text>'
            f'<path d="M{gauche:.2f},{base + 0.15 * t:.2f}H{gauche + L:.2f}" stroke="{{c}}" '
            f'stroke-width="{0.07 * t:.2f}"/>')
    print(f"   {len(capitales_mm)} capitales")

    # villes, des plus peuplées aux moins peuplées, tant qu'il y a de la place
    mesure_ville = mesureur(*VILLE_POLICE)
    villes_svg = []
    for nom, pop, pt in villes_mm:
        x, y = pt.x, pt.y
        reservee = pop >= VILLE_POP_RESERVEE
        rayon, corps = RAYON_VILLE_MM[reservee], VILLE_MM[reservee]
        point = pt.buffer(rayon + 0.4)
        if not reservee and any(point.intersects(o) for o in occupe):
            continue
        place = placer_nom(x, y, mesure_ville(nom), (corps,), rayon + 0.6)
        if not place:
            continue
        if not reservee:
            occupe.append(point)
        gauche, centre, t, L = place
        villes_svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{rayon}" fill="{{c}}"/>'
                          f'<text x="{gauche:.2f}" y="{centre + 0.3 * t:.2f}" font-family="{POLICE}" '
                          f'font-weight="500" font-size="{t}" fill="{{c}}">{nom}</text>')
    print(f"   {len(villes_svg)} villes de plus de {VILLE_POP_MIN:,} habitants (sur {len(villes_mm)})")

    # canevas : lignes denses en lon/lat, découpées à la calotte visible, puis en perspective
    lignes_geo = [LineString([(lon, lat) for lat in np.arange(-10, (90 if lon % 30 == 0 else 80) + 0.01, 0.25)])
                  for lon in range(-180, 180, PAS_GRATICULE)]
    paralleles = range(PAS_GRATICULE, 90, PAS_GRATICULE)
    lignes_geo += [LineString([(lon, lat) for lon in np.arange(-180, 180.01, 0.25)]) for lat in paralleles]
    graticule = unary_union([projeter(projeter(l, geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper)
                             for l in lignes_geo])
    graticule_mm = vers_mm(graticule).intersection(globe_mm)

    # degrés là où les méridiens touchent le bas de la carte, et les parallèles les côtés
    mesure_deg = mesureur(POLICE_FICHIER[0], "Regular")
    bas = LineString([(MARGE, PAGE_H - MARGE), (PAGE_L - MARGE, PAGE_H - MARGE)])
    gauche_ = LineString([(MARGE, MARGE), (MARGE, PAGE_H - MARGE)])
    droite_ = LineString([(PAGE_L - MARGE, MARGE), (PAGE_L - MARGE, PAGE_H - MARGE)])
    degres_svg = []

    def degre(texte, x, y, ancre):
        L, t = mesure_deg(texte) * DEGRES_MM, DEGRES_MM
        x0 = {"start": x, "middle": x - L / 2, "end": x - L}[ancre]
        e = box(x0 - 0.3, y - 0.75 * t, x0 + L + 0.3, y + 0.2 * t)
        if globe_mm.contains(e) and not any(e.intersects(o) for o in occupe):
            occupe.append(e)
            degres_svg.append(f'<text x="{x:.2f}" y="{y:.2f}" font-family="{POLICE}" font-size="{t}" '
                              f'text-anchor="{ancre}" fill="{{c}}">{texte}</text>')

    for lon in range(-180, 180, PAS_GRATICULE):
        l = vers_mm(projeter(projeter(LineString([(lon, la) for la in np.arange(-10, 80.01, 0.25)]),
                                      geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper))
        x = l.intersection(bas)
        if not x.is_empty and x.geom_type == "Point":
            texte = f"{abs(lon)}° {'E' if lon > 0 else 'O'}" if lon else "0°"
            degre(texte, x.x + 0.8, PAGE_H - MARGE - 1.2, "start")
    for lat in paralleles:
        l = vers_mm(projeter(projeter(LineString([(lo, lat) for lo in np.arange(-180, 180.01, 0.25)]),
                                      geo_vers_aeqd).intersection(calotte), aeqd_vers_nsper))
        for bord, x_txt, ancre in ((gauche_, MARGE + 1.2, "start"), (droite_, PAGE_L - MARGE - 1.2, "end")):
            x = l.intersection(bord)
            if not x.is_empty and x.geom_type == "Point":
                degre(f"{lat}° N", x_txt, x.y - 1.0, ancre)

    # interruption autour des noms (pays, capitales, degrés)
    graticule_mm = graticule_mm.difference(unary_union(occupe).buffer(BLANC_AUTOUR_NOMS_MM))
    routes_mm = (vers_mm(routes).intersection(terre_nette).simplify(SIMPLIF_MM)
                 .difference(unary_union(occupe).buffer(BLANC_AUTOUR_NOMS_MM)))
    print(f"   canevas tous les {PAS_GRATICULE}°, {len(degres_svg)} degrés en bordure")

    def plein(g, c):
        return f'<path d="{d_poly(g)}" fill="{c}" fill-rule="evenodd" stroke="none"/>'

    def legende(texte, c):
        return (f'<text x="{PAGE_L / 2}" y="{PAGE_H - MARGE / 2 + 1.5}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="4" text-anchor="middle" fill="{c}">{texte}</text>')

    def traits(g, ep, c):
        return (f'<path d="{d_lignes(g)}" fill="none" stroke="{c}" stroke-width="{ep}" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')

    contenus = {"terre": plein(terres_mm, "{c}"), "mer": plein(mer_mm, "{c}"),
                "relief": f'<path d="{trame_d}" fill="{{c}}" stroke="none"/>',
                "noir": traits(graticule_mm, TRAIT_GRATICULE_MM, "{c}")
                + traits(routes_mm, TRAIT_ROUTE_MM, "{c}")
                + traits(frontieres_mm, TRAIT_FRONTIERE_MM, "{c}") + "".join(noms_svg)
                + "".join(capitales_svg) + "".join(villes_svg) + "".join(degres_svg)}
    noms = [("calque_1_terre", "terre", "1/4 — TERRE (ocre clair)"),
            ("calque_2_relief", "relief", "2/4 — RELIEF (marron foncé, trame)"),
            ("calque_3_mer", "mer", "3/4 — MER ET FLEUVES (bleu)"),
            ("calque_4_noir", "noir", "4/4 — FRONTIÈRES ET NOMS (noir)")]

    print("Écriture :")
    apercu = [("papier", "papier", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>')]
    for _, cle, lab in noms:
        apercu.append((cle, lab, contenus[cle].replace("{c}", COULEURS[cle])))
    apercu.append(("reperes", "repères", reperes("#000")))
    ecrire("europe_apercu", svg(apercu, "Europe vue de l'espace — aperçu"))

    for nom, cle, lab in noms:
        c = [("fond", "fond", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>'),
             (cle, lab, contenus[cle].replace("{c}", "#000000")),
             ("reperes", "repères", reperes("#000") + legende(lab, "#000"))]
        ecrire(nom, svg(c, f"Europe vue de l'espace — {lab}"))


if __name__ == "__main__":
    main()
