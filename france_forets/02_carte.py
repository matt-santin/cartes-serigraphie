"""Carte de France des forêts, en 4 calques de sérigraphie.

Le cadre est centré sur la France ; les pays voisins visibles reçoivent le
même traitement (terre + forêt), avec leurs frontières en noir.

Ordre d'impression :
  1. mer    (bleu)       : le fond, qui déborde légèrement sous les terres (trapping)
  2. terre  (terre)      : toutes les terres, en aplat
  3. forêt  (vert foncé) : les zones boisées, imprimées par-dessus la terre
  4. noir                : départements, frontières, côtes des voisins et cadre

Sorties dans sortie/ : un SVG et un PDF par calque (noir sur blanc),
plus un aperçu en couleur.
"""
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy import ndimage
from shapely import affinity
from shapely.geometry import box, shape, MultiPolygon, Polygon, LineString, MultiLineString
from shapely.ops import unary_union

ICI = Path(__file__).parent
DATA = ICI / "data"
SORTIE = ICI / "sortie"
SORTIE.mkdir(exist_ok=True)

# --- Paramètres d'impression (mm) -------------------------------------------
PAGE_L, PAGE_H = 500, 500        # format de la feuille
MARGE = 25                       # marge autour du cadre (repères dedans)
PIXEL_MM = 0.2                   # résolution de calcul de la forêt
LISSAGE_MM = 0.5                 # rayon de lissage de la densité
SEUIL_DENSITE = 0.50             # part de couvert arboré au-delà de laquelle c'est « boisé »
AIRE_MIN_MM2 = 0.8               # taches et trous plus petits : supprimés
SIMPLIF_MM = 0.05                # simplification des contours
TRAP_MM = 0.3                    # débord de la mer sous la terre
TRAIT_DEPT_MM = 0.35             # épaisseur des limites de départements
TRAIT_CADRE_MM = 1.0

COULEURS = {"mer": "#3d6f9e", "terre": "#d8c29a", "foret": "#1e4a2c", "noir": "#111111"}
CRS = "EPSG:2154"  # Lambert-93


# --- Géométrie de la mise en page ---------------------------------------------
def charger():
    depts = gpd.read_file(DATA / "departements.geojson").to_crs(CRS)
    pays = gpd.read_file(DATA / "ne_countries" / "ne_10m_admin_0_countries.shp")
    pays = pays.clip(box(-12, 38, 16, 55)).to_crs(CRS)  # découpe avant projection
    osm = gpd.read_file(DATA / "lacs_osm.geojson")
    ne = gpd.read_file(DATA / "ne_lakes" / "ne_10m_lakes.shp")
    ne = ne[~ne.name.isin(["Lake Geneva", "Bodensee"])]  # remplacés par OSM
    ne_eu = gpd.read_file(DATA / "ne_lakes_europe" / "ne_10m_lakes_europe.shp")
    lacs = gpd.GeoDataFrame(geometry=list(osm.geometry) + list(ne.geometry) + list(ne_eu.geometry),
                            crs="EPSG:4326")
    lacs = lacs.clip(box(-12, 38, 16, 55)).to_crs(CRS)
    return depts, pays, lacs


def mise_en_page(depts):
    """Renvoie (fonction terrain->mm, emprise terrain du cadre, échelle m/mm)."""
    x0, y0, x1, y1 = depts.total_bounds
    cadre_l, cadre_h = PAGE_L - 2 * MARGE, PAGE_H - 2 * MARGE
    pad = 0.04
    ech = max((x1 - x0) * (1 + 2 * pad) / cadre_l, (y1 - y0) * (1 + 2 * pad) / cadre_h)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    emprise = (cx - ech * cadre_l / 2, cy - ech * cadre_h / 2,
               cx + ech * cadre_l / 2, cy + ech * cadre_h / 2)

    def vers_mm(geom):
        g = affinity.translate(geom, -emprise[0], -emprise[3])
        g = affinity.scale(g, 1 / ech, -1 / ech, origin=(0, 0))
        return affinity.translate(g, MARGE, MARGE)

    return vers_mm, emprise, ech


# --- Forêt --------------------------------------------------------------------
def foret_mm(emprise, ech, terres_mm, vers_mm):
    taille_px = PIXEL_MM * ech
    largeur = int(round((emprise[2] - emprise[0]) / taille_px))
    hauteur = int(round((emprise[3] - emprise[1]) / taille_px))
    transform = rasterio.transform.from_bounds(*emprise, largeur, hauteur)

    dens = np.full((hauteur, largeur), np.nan, dtype="float32")
    with rasterio.open(DATA / "arbres_wgs84.tif") as src:
        a = src.read(1).astype("float32")
        a[a == 255] = np.nan
        reproject(a, dens, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=np.nan, dst_transform=transform, dst_crs=CRS,
                  dst_nodata=np.nan, resampling=Resampling.average)
    dens = np.nan_to_num(dens / 100.0)

    sigma = LISSAGE_MM / PIXEL_MM
    dens = ndimage.gaussian_filter(dens, sigma)
    masque = dens >= SEUIL_DENSITE

    # nettoyage : petites taches et petits trous
    px_min = int(AIRE_MIN_MM2 / PIXEL_MM ** 2)
    masque = _sans_petits(masque, px_min)
    masque = ~_sans_petits(~masque, px_min)

    # contours doux : on re-floute le masque propre et on le suréchantillonne x4
    # avant vectorisation, pour éviter les marches d'escalier des pixels
    k = 4
    fin = ndimage.zoom(ndimage.gaussian_filter(masque.astype("float32"), 1.0), k, order=1) >= 0.5
    transform_fin = transform * transform.scale(1 / k)
    polys = [shape(g) for g, v in features.shapes(fin.astype("uint8"), mask=fin,
                                                     transform=transform_fin) if v == 1]
    foret = vers_mm(unary_union(polys))
    foret = foret.simplify(SIMPLIF_MM).buffer(0).intersection(terres_mm)
    return _polygones(foret)


def _sans_petits(m, px_min):
    lab, n = ndimage.label(m)
    tailles = np.bincount(lab.ravel())
    garder = tailles >= px_min
    garder[0] = False
    return garder[lab]


def _polygones(g):
    if g.is_empty:
        return MultiPolygon()
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    if isinstance(g, MultiPolygon):
        return g
    return MultiPolygon([p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)])


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
    geoms = g.geoms if hasattr(g, "geoms") else [g]
    for l in geoms:
        if isinstance(l, (LineString,)):
            c = np.asarray(l.coords)
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
            f'width="{PAGE_L}mm" height="{PAGE_H}mm" viewBox="0 0 {PAGE_L} {PAGE_H}">'
            f'<title>{titre}</title>{corps}</svg>\n')


def ecrire(nom, contenu):
    chemin_svg = SORTIE / f"{nom}.svg"
    chemin_svg.write_text(contenu)
    subprocess.run(["inkscape", str(chemin_svg), "--export-type=pdf",
                    f"--export-filename={SORTIE / f'{nom}.pdf'}"],
                   check=True, capture_output=True)
    print("  ", chemin_svg.name, "+ pdf")


# --- Principal ----------------------------------------------------------------
def main():
    depts, pays, lacs = charger()
    vers_mm, emprise, ech = mise_en_page(depts)
    print(f"Échelle : 1 mm = {ech / 1000:.2f} km  (1:{ech * 1000:,.0f})")

    cadre = box(MARGE, MARGE, PAGE_L - MARGE, PAGE_H - MARGE)
    zone = box(*emprise).buffer(50_000)

    lacs = unary_union(lacs.geometry.buffer(0))
    depts["geometry"] = depts.geometry.buffer(0).difference(lacs)  # Léman en eau
    france = unary_union(depts.geometry)
    france_ne = unary_union(pays[pays.ADMIN == "France"].geometry.buffer(0))
    pays_zone = pays[(pays.ADMIN != "France") & pays.intersects(zone)].copy()
    pays_zone["geometry"] = pays_zone.geometry.buffer(0).intersection(zone)
    voisins = unary_union(pays_zone.geometry)
    # bouche les interstices entre les deux sources le long des frontières terrestres
    joint = voisins.buffer(2000).intersection(france.buffer(15_000))
    voisins = unary_union([voisins, joint]).difference(france).difference(lacs)

    france_mm = vers_mm(france).intersection(cadre)
    voisins_mm = vers_mm(voisins).intersection(cadre)
    terres_mm = unary_union([france_mm, voisins_mm])

    # la mer déborde sous toutes les côtes
    mer_mm = cadre.difference(terres_mm).buffer(TRAP_MM).intersection(cadre)

    print("Calcul de la forêt…")
    foret = foret_mm(emprise, ech, terres_mm, vers_mm)
    part = foret.intersection(france_mm).area / france_mm.area
    print(f"   {len(foret.geoms)} polygones, {100 * part:.0f} % de la France en vert")

    # frontières et côtes des voisins (Natural Earth), sauf les segments partagés
    # avec la France, où les limites des départements font foi (pas de double trait)
    bords_voisins = (unary_union(pays_zone.geometry.boundary)
                     .difference(france_ne.boundary.buffer(50))
                     .difference(france.buffer(300)).difference(lacs))
    limites = unary_union([vers_mm(g).boundary for g in depts.geometry]
                          + [vers_mm(bords_voisins), vers_mm(lacs.boundary)])
    limites = limites.intersection(cadre)

    def plein(g, c):
        return f'<path d="{d_poly(g)}" fill="{c}" fill-rule="evenodd" stroke="none"/>'

    def traits(c):
        return (f'<path d="{d_lignes(limites)}" fill="none" stroke="{c}" '
                f'stroke-width="{TRAIT_DEPT_MM}" stroke-linejoin="round" stroke-linecap="round"/>'
                f'<rect x="{MARGE}" y="{MARGE}" width="{PAGE_L - 2 * MARGE}" height="{PAGE_H - 2 * MARGE}" '
                f'fill="none" stroke="{c}" stroke-width="{TRAIT_CADRE_MM}"/>')

    def legende(texte, c):
        return (f'<text x="{PAGE_L / 2}" y="{PAGE_H - MARGE / 2 + 1.5}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="4" text-anchor="middle" fill="{c}">{texte}</text>')

    contenus = {
        "mer": plein(mer_mm, "{c}"),
        "terre": plein(terres_mm, "{c}"),
        "foret": plein(foret, "{c}"),
        "noir": traits("{c}"),
    }
    noms = [("calque_1_mer", "mer", "1/4 — MER (bleu)"),
            ("calque_2_terre", "terre", "2/4 — TERRE"),
            ("calque_3_foret", "foret", "3/4 — FORÊT (vert foncé)"),
            ("calque_4_noir", "noir", "4/4 — LIMITES (noir)")]

    print("Écriture :")
    # aperçu couleur, un calque Inkscape par encre
    apercu = [("papier", "papier", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>')]
    for _, cle, lab in noms:
        apercu.append((cle, lab, contenus[cle].replace("{c}", COULEURS[cle])))
    apercu.append(("reperes", "repères", reperes("#000")))
    ecrire("france_forets_apercu", svg(apercu, "France des forêts — aperçu"))

    # un fichier par calque, noir sur blanc
    for nom, cle, lab in noms:
        c = [("fond", "fond", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>'),
             (cle, lab, contenus[cle].replace("{c}", "#000000")),
             ("reperes", "repères", reperes("#000") + legende(lab, "#000"))]
        ecrire(nom, svg(c, f"France des forêts — {lab}"))


if __name__ == "__main__":
    main()
