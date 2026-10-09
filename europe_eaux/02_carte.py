"""Les eaux de l'Europe, vues de l'espace, en 4 calques de sérigraphie.

Même vue, même format et même relief que la carte politique (europe/02_carte.py,
dont on réutilise les fonctions) : projection perspective verticale, l'horizon
courbe en haut, la glace de l'Arctique et la neige en réserve.

Ordre d'impression (la plus claire d'abord) :
  1. terre  (ocre clair) : toutes les terres ; déborde de 0,3 mm sous la mer
  2. relief (marron foncé) : estompage du relief en trame de points
  3. eaux   (bleu) : mers, lacs et rivières (HydroRIVERS), d'autant plus épaisses
     que leur débit est fort ; noms des rivières en italique bleu ; noms des mers
     en réserve (le papier) dans le bleu
  4. noir : lignes de partage des eaux (entre les versants des différentes mers,
     et entre les grands bassins), canevas, légende

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
from scipy import ndimage
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union

ICI = Path(__file__).parent
DATA = ICI / "data"
SORTIE = ICI / "sortie"
SORTIE.mkdir(exist_ok=True)

# fond commun : fonctions et données de la carte politique
_spec = importlib.util.spec_from_file_location("europe_carte", ICI.parent / "europe" / "02_carte.py")
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)
PAGE_L, PAGE_H, MARGE = E.PAGE_L, E.PAGE_H, E.MARGE   # 700 × 500 mm, comme la carte politique

# --- Rivières (HydroRIVERS) -------------------------------------------------------
DEBIT_MIN = 8                    # débit moyen minimal (m³/s) : écarte aussi les oueds du Sahara
FACTEUR_RIVIERE = 0.085          # épaisseur (mm) = FACTEUR × débit^0,25 …
TRAIT_RIVIERE_MM = (0.2, 0.9)    # … bornée (Volga, Danube : 0,9 mm)
LISSAGE_RIVIERE_MM = 0.08       # simplification avant lissage des rivières
PAS_TRAIT_MM = 0.05              # épaisseurs arrondies (un tracé par épaisseur)

# --- Lignes de partage des eaux (HydroBASINS niveau 5) ----------------------------
# Chaque bassin principal (fleuve et ses affluents jusqu'à la mer) appartient au
# versant de la mer où il débouche (mers de l'OHI) ; les bassins endoréiques forment
# le versant de la Caspienne ou un versant « intérieur ». Deux niveaux de lignes :
# entre versants (tirets épais) et entre grands bassins d'un même versant (pointillé).
VERSANTS = {
    "atlantique": ["North Atlantic Ocean", "Bay of Biscay", "Celtic Sea", "English Channel", "Bristol Channel",
                   "Irish Sea and St. George's Channel", "Inner Seas off the West Coast of Scotland",
                   "Labrador Sea", "Davis Strait"],
    "mer du Nord": ["North Sea", "Skagerrak", "Kattegat"],
    "baltique": ["Baltic Sea", "Gulf of Riga", "Gulf of Finland", "Gulf of Bothnia"],
    "arctique": ["Arctic Ocean", "Norwegian Sea", "Barentsz Sea", "White Sea", "Kara Sea", "Greenland Sea",
                 "Lincoln Sea", "Chukchi Sea", "Bering Sea", "North Pacific Ocean"],
    "méditerranée": ["Strait of Gibraltar", "Alboran Sea", "Balearic (Iberian Sea)", "Ligurian Sea",
                     "Mediterranean Sea - Western Basin", "Mediterranean Sea - Eastern Basin", "Tyrrhenian Sea",
                     "Ionian Sea", "Adriatic Sea", "Aegean Sea", "Sea of Marmara"],
    "mer Noire": ["Black Sea", "Sea of Azov"],
    "océan Indien": ["Persian Gulf", "Red Sea", "Gulf of Aqaba", "Gulf of Suez", "Gulf of Aden", "Gulf of Oman",
                     "Arabian Sea", "Bay of Bengal"],
}
DIST_CASPIENNE_DEG = 0.3         # exutoire à moins de 0,3° de la Caspienne : versant caspien
BASSIN_MIN_KM2 = 40_000          # bassins plus grands : entourés d'un pointillé (Seine, Pô, Èbre…)
ENDO_MIN_KM2 = 20_000            # petits bassins endoréiques : fondus dans leurs voisins
PAS_PARTAGE_MM = 0.2             # grille de calcul des lignes (puis lissage)
LISSAGE_PARTAGE_PX = 2.0        # simplification avant lissage (en pixels de la grille)
LONGUEUR_MIN_PARTAGE_MM = 4      # bouts de ligne plus courts : supprimés
TRAIT_VERSANT_MM, TIRETS_VERSANT_MM = 0.6, (2.6, 1.1)
TRAIT_BASSIN_MM, TIRETS_BASSIN_MM = 0.4, (0.01, 1.0)   # points ronds tous les mm

# --- Noms ---------------------------------------------------------------------------
# Mers : capitales italiques espacées, en réserve dans le bleu (le papier), le long du
# parallèle (lon, lat, corps mm) ou droites et inclinées (lon, lat, corps, angle)
MERS = [("OCÉAN ATLANTIQUE", -15.5, 44.5, 9.0), ("MER DU NORD", 3.6, 56.4, 6.0),
        ("MER BALTIQUE", 18.8, 55.6, 4.2, -25), ("MER DE NORVÈGE", 3.0, 67.5, 7.0),
        ("MER DE BARENTS", 36.0, 73.0, 5.5), ("MER BLANCHE", 39.0, 65.4, 2.6),
        ("MER MÉDITERRANÉE", 5.0, 38.6, 6.5), ("MER NOIRE", 34.0, 43.4, 6.5),
        ("MER CASPIENNE", 50.8, 42.2, 4.0, 68), ("MER ADRIATIQUE", 15.8, 42.9, 3.6, 35),
        ("MER ÉGÉE", 25.0, 38.8, 3.2, -70), ("MER TYRRHÉNIENNE", 12.0, 39.8, 3.6, -40),
        ("MER IONIENNE", 18.8, 37.3, 3.8), ("GOLFE DE GASCOGNE", -5.0, 45.6, 3.8),
        ("MANCHE", -2.6, 49.9, 3.2), ("MER D'AZOV", 36.7, 46.1, 2.6), ("GOLFE DE BOTNIE", 20.4, 62.4, 3.2, -58),
        ("MER DU GROENLAND", 0.0, 73.5, 4.0)]
MER_POLICE = ("/System/Library/Fonts/HelveticaNeue.ttc", "Bold Italic")
ESPACEMENT_MER = 0.18            # espacement des lettres (em)
# Rivières : noms Natural Earth (rang ≤ RANG_NOMS), en italique bleu, le long de la
# rivière, du côté où ils ne touchent ni l'eau ni un autre nom
RANG_NOMS = 8
RIVIERE_MM = {4: 5.0, 6: 4.2, 99: 3.5}   # corps selon le rang (≤ clé)
RIVIERE_POLICE = ("/System/Library/Fonts/HelveticaNeue.ttc", "Medium Italic")
ESPACEMENT_RIVIERE = 0.06
ECART_NOM_MM = {3: 1.6, 5: 1.3, 99: 1.0}   # écart entre l'axe de la rivière et son nom, selon le rang
ECART_ARC_MM = 0.8               # écart maximal entre la rivière et l'arc qui porte le nom
COURBURE_MAX = 0.96              # corde / longueur minimale de l'arc qui porte le nom
TRAIT_COUPE_MM = 0.4             # les rivières plus fines s'interrompent sous les noms ;
                                 # les plus épaisses ne sont jamais touchées
NOMS_RIVIERES = {"Sâne": "Saône", "Lule lv": "Lule", "Dvina septentrionale": "Dvina du Nord",
                 "Rhin inférieur": None, "Waal": None, "Lek": None, "IJssel": None, "Ráckevei-Duna": None,
                 "Sió": None, "Vorma": None, "Mincio": None, "Oich": None, "Loch Oich": None, "Ness": None,
                 "Luiro": None, "Louza": None, "Malaya Ob": None, "Rivière Stora Lule": None, "Borcea": None,
                 "Bras de Saint Georges": None, "Bras de Chilia": None, "Bras de Sulina": None,
                 "Damiette": None, "Rosetta Branch": None, "Lac Timsah": None, "Shatt Al Gharraf": None,
                 "Shatt al Hillah": None, "Hindiyah Channel": None, "Atshan": None, "Talkeh": None}

COULEURS = {"terre": "#e8d08c", "relief": "#6b4a2b", "eaux": "#3d6f9e", "noir": "#1a1a1a"}


def chaikin(coords, n=3):
    """Lissage de Chaikin, extrémités fixes."""
    c = np.asarray(coords)
    for _ in range(n):
        if len(c) < 3:
            break
        q = 0.75 * c[:-1] + 0.25 * c[1:]
        r = 0.25 * c[:-1] + 0.75 * c[1:]
        c = np.vstack([c[:1], np.column_stack([q, r]).reshape(-1, 2)[1:-1], c[-1:]])
    return c


def arc(seg, n=40, prolonge=0.0):
    """Arc régulier (parabole dans le repère de la corde) qui suit le tronçon seg :
    une ligne de base sans coudes pour les noms, prolongée de `prolonge` mm à chaque
    bout. Renvoie (arc, écart max en mm)."""
    c = np.array([seg.interpolate(f, normalized=True).coords[0] for f in np.linspace(0, 1, 30)])
    corde = c[-1] - c[0]
    u = corde / np.hypot(*corde)
    v = np.array([-u[1], u[0]])
    s, h = (c - c[0]) @ u, (c - c[0]) @ v
    coef = np.polyfit(s, h, 2)
    si = np.linspace(s.min() - prolonge, s.max() + prolonge, n)
    pts = c[0] + np.outer(si, u) + np.outer(np.polyval(coef, si), v)
    return LineString(pts), float(np.abs(h - np.polyval(coef, s)).max())


def lignes_de(g):
    """Les LineString d'une géométrie quelconque."""
    if g.is_empty:
        return []
    if isinstance(g, LineString):
        return [g]
    return [l for p in g.geoms for l in lignes_de(p)]


# --- Données hydrographiques --------------------------------------------------------
def charger_rivieres():
    morceaux = []
    for f in sorted(DATA.glob("rivieres_*/*/*.shp")):
        morceaux.append(gpd.read_file(f, where=f"DIS_AV_CMS >= {DEBIT_MIN}",
                                      columns=["DIS_AV_CMS", "UPLAND_SKM"]))
    r = pd.concat(morceaux, ignore_index=True)
    ep = np.clip(FACTEUR_RIVIERE * r.DIS_AV_CMS ** 0.25, *TRAIT_RIVIERE_MM)
    r["trait"] = np.round(ep / PAS_TRAIT_MM) * PAS_TRAIT_MM
    print(f"   rivières : {len(r):,} tronçons de plus de {DEBIT_MIN} m³/s")
    return [(t, shapely.multilinestrings(list(g.geometry))) for t, g in r.groupby("trait")]


def caspienne_geo():
    """La Caspienne : le trou des terres de Natural Earth qui contient (51° E, 42° N)."""
    terres = gpd.read_file(E.DATA / "ne_10m_land" / "ne_10m_land.shp", bbox=(45, 35, 56, 48))
    pt = Point(51, 42)
    for g in terres.geometry:
        for p in getattr(g, "geoms", [g]):
            for trou in p.interiors:
                if shapely.Polygon(trou).contains(pt):
                    return shapely.Polygon(trou)
    raise ValueError("Caspienne introuvable")


def charger_bassins(fichiers=None):
    """Bassins HydroBASINS (niveau 5 par défaut), avec le versant et l'unité (grand bassin
    ou reste du versant)."""
    fichiers = fichiers or sorted(DATA.glob("bassins_*/*.shp"))
    b = pd.concat([gpd.read_file(f) for f in fichiers], ignore_index=True)
    mers = gpd.read_file(DATA / "mers_iho.geojson")
    versant_mer = {mer: v for v, liste in VERSANTS.items() for mer in liste}
    arbre = shapely.STRtree(mers.geometry.simplify(0.02).values)   # simplifiées : 3,5 M de sommets sinon
    caspienne = caspienne_geo()

    exutoires = b[b.HYBAS_ID == b.MAIN_BAS]
    versant = {}
    for _, e in exutoires.iterrows():
        if e.geometry.distance(caspienne) < DIST_CASPIENNE_DEG:
            versant[e.MAIN_BAS] = "caspienne"
        elif e.ENDO > 0:
            versant[e.MAIN_BAS] = "intérieur" if e.UP_AREA >= ENDO_MIN_KM2 else None
        else:
            versant[e.MAIN_BAS] = versant_mer[mers.name.iloc[arbre.query_nearest(e.geometry)[0]]]
    aire = exutoires.set_index("MAIN_BAS").UP_AREA
    b["versant"] = b.MAIN_BAS.map(versant)
    grand = b.MAIN_BAS.map(aire >= BASSIN_MIN_KM2) & ~b.versant.isin(["intérieur", None])
    b["unite"] = np.where(grand, b.MAIN_BAS.astype(str), "reste " + b.versant.astype(str))
    b.loc[b.versant.isna(), "unite"] = None
    n_grands = b[grand].MAIN_BAS.nunique()
    print(f"   bassins : {len(b)} sous-bassins, {n_grands} grands bassins, "
          f"{b.versant.nunique()} versants")
    return b


def lignes_partage(bassins, vers_mm_geo, terre_mm):
    """Lignes de partage des eaux, en mm : (entre versants, entre grands bassins).

    Les bassins sont rastérisés sur une grille fine de la carte (les jointures entre
    régions HydroSHEDS ne sont pas exactes) ; les trous et la mer prennent l'unité
    la plus proche ; les limites entre unités sont suivies sur les bords des pixels,
    puis lissées. On ne garde que ce qui tombe sur la terre."""
    p = PAS_PARTAGE_MM
    forme = (int((PAGE_H - 2 * MARGE) / p), int((PAGE_L - 2 * MARGE) / p))
    t_mm = rasterio.Affine(p, 0, MARGE, 0, p, MARGE)
    unites = sorted(u for u in bassins.unite.dropna().unique())
    num = {u: i + 1 for i, u in enumerate(unites)}
    versant_unite = np.zeros(len(unites) + 1, dtype="int32")
    noms_versants = sorted(bassins.versant.dropna().unique())
    for u in unites:
        v = bassins.versant[bassins.unite == u].iloc[0]
        versant_unite[num[u]] = noms_versants.index(v) + 1

    formes = []
    for g, u in zip(bassins.geometry, bassins.unite):
        if not isinstance(u, str):   # petit bassin endoréique : pris par ses voisins
            continue
        g = vers_mm_geo(g)
        if not g.is_empty:
            formes.append((g, num[u]))
    L = rasterio.features.rasterize(formes, out_shape=forme, transform=t_mm, dtype="int32")
    idx = ndimage.distance_transform_edt(L == 0, return_distances=False, return_indices=True)
    L = L[tuple(idx)]
    V = versant_unite[L]

    def segments(masque_h, masque_v):
        # bords verticaux entre (i, j) et (i, j+1), horizontaux entre (i, j) et (i+1, j), en pixels
        i, j = np.nonzero(masque_h)
        a = np.stack([np.column_stack([j + 1, i]), np.column_stack([j + 1, i + 1])], axis=1)
        i, j = np.nonzero(masque_v)
        b = np.stack([np.column_stack([j, i + 1]), np.column_stack([j + 1, i + 1])], axis=1)
        segs = shapely.linestrings(np.concatenate([a, b]).astype(float))
        fusion = shapely.line_merge(shapely.multilinestrings(segs))
        out = []
        for l in lignes_de(fusion):
            c = chaikin(np.asarray(l.simplify(LISSAGE_PARTAGE_PX).coords), 4) * p + MARGE
            out.append(LineString(c))
        return shapely.MultiLineString(out)

    dh, dv = L[:, :-1] != L[:, 1:], L[:-1, :] != L[1:, :]
    vh, vv = V[:, :-1] != V[:, 1:], V[:-1, :] != V[1:, :]
    res = []
    for g in (segments(vh, vv), segments(dh & ~vh, dv & ~vv)):
        g = shapely.line_merge(g.intersection(terre_mm))
        res.append(shapely.MultiLineString([l for l in lignes_de(g) if l.length >= LONGUEUR_MIN_PARTAGE_MM]))
    print(f"   partage des eaux : {len(res[0].geoms)} lignes entre versants, "
          f"{len(res[1].geoms)} entre grands bassins")
    return res


# --- Principal ----------------------------------------------------------------------
def main():
    calotte, theta = E.calotte_visible()
    print(f"Horizon à {theta:.1f}° du centre de la vue")
    terres, lacs, _, _, _, _, _, glaciers, _ = E.charger(theta)
    banquise, crs_banquise = E.banquise_mediane()
    rivieres = charger_rivieres()
    bassins = charger_bassins()

    geo_vers_aeqd = Transformer.from_crs(E.GEO, E.AEQD, always_xy=True)
    aeqd_vers_nsper = Transformer.from_crs(E.AEQD, E.NSPER, always_xy=True)
    projeter = E.projeter

    print("Projection…")
    terres = projeter(terres, geo_vers_aeqd).buffer(0).intersection(calotte)
    lacs = projeter(lacs, geo_vers_aeqd).buffer(0).intersection(calotte)
    terres = terres.difference(lacs)
    horizon = projeter(calotte.segmentize(E.R_TERRE * 0.002), aeqd_vers_nsper)
    terres = projeter(terres, aeqd_vers_nsper).buffer(0)
    banquise_vers_aeqd = Transformer.from_crs(crs_banquise, E.AEQD, always_xy=True)
    banquise = projeter(projeter(banquise.segmentize(20_000), banquise_vers_aeqd).buffer(0)
                        .intersection(calotte), aeqd_vers_nsper).buffer(0)
    glaciers = projeter(projeter(glaciers, geo_vers_aeqd).buffer(0).intersection(calotte),
                        aeqd_vers_nsper).buffer(0)

    vers_mm, ech, top_m = E.mise_en_page(horizon)
    print(f"Au centre de la vue : 1 mm = {ech / 1000:.1f} km  (1:{ech * 1000:,.0f})")

    def geo_mm(g, surface=False):
        """Géographique -> mm, découpé à la calotte visible."""
        g = projeter(g, geo_vers_aeqd)
        g = (g.buffer(0) if surface else g).intersection(calotte)
        if g.is_empty:
            return g
        g = vers_mm(projeter(g, aeqd_vers_nsper))
        return g.buffer(0) if surface else g

    # --- terre, mer, glace, neige, relief : comme la carte politique
    cadre = box(MARGE, MARGE, PAGE_L - MARGE, PAGE_H - MARGE)
    globe_mm = vers_mm(horizon).intersection(cadre)
    r = E.FIN_MM / 2
    terres_mm = vers_mm(terres).simplify(E.SIMPLIF_MM).buffer(0)
    terres_mm = terres_mm.buffer(-r).buffer(r).buffer(r).buffer(-r)
    bande = vers_mm(horizon).difference(vers_mm(horizon).buffer(-3 * r))
    terres_mm = terres_mm.union(terres_mm.buffer(3 * r).intersection(bande))
    terres_mm = E.nettoyer(terres_mm.intersection(globe_mm), E.AIRE_MIN_MM2)
    mer_mm = E.nettoyer(globe_mm.difference(terres_mm), E.AIRE_MIN_MM2)
    terres_mm = E._polygones(globe_mm.difference(mer_mm).buffer(E.TRAP_MM).intersection(globe_mm))
    terre_nette = globe_mm.difference(mer_mm)

    mer_seule = globe_mm.difference(terre_nette)
    glace_mm = unary_union([vers_mm(banquise).intersection(mer_seule),
                            vers_mm(glaciers).intersection(terre_nette)])
    glace_mm = glace_mm.simplify(E.SIMPLIF_MM).buffer(0)
    glace_mm = glace_mm.buffer(-r).buffer(r).buffer(r).buffer(-r)
    glace_mm = E.nettoyer(glace_mm.intersection(globe_mm), E.AIRE_MIN_GLACE_MM2)
    terres_mm = E.nettoyer(terres_mm.difference(glace_mm), E.AIRE_MIN_MM2)
    mer_mm = E.nettoyer(mer_mm.difference(glace_mm), E.AIRE_MIN_MM2)
    print(f"   glace : {glace_mm.area:,.0f} mm² en réserve")

    print("Relief…")
    forme, t_nsper, t_mm = E.grille_carte(ech, top_m)
    z_carte, ombre_carte = E.relief_carte(calotte.bounds[2], forme, t_nsper)
    neige_mm = E.vectoriser(z_carte >= E.NEIGE_M, t_mm).intersection(terre_nette).difference(glace_mm)
    neige_mm = E.nettoyer(neige_mm, E.AIRE_MIN_MM2)
    terres_mm = E.nettoyer(terres_mm.difference(neige_mm), E.AIRE_MIN_MM2)
    zone_relief = terre_nette.difference(glace_mm).difference(neige_mm.buffer(0.2))
    trame_d = E.trame(ombre_carte, t_mm, zone_relief)

    # --- rivières : sur la terre libre de glace, par épaisseur
    print("Rivières…")
    terre_libre = terre_nette.difference(glace_mm)
    shapely.prepare(terre_libre)
    rivieres_mm = []
    for t, g in rivieres:
        g = geo_mm(g)
        g = shapely.line_merge(g.intersection(terre_libre))
        # pas de marches d'escalier (grille de 15″ de HydroSHEDS) : simplification puis lissage
        g = shapely.MultiLineString([LineString(chaikin(np.asarray(l.simplify(LISSAGE_RIVIERE_MM).coords), 2))
                                     for l in lignes_de(g) if len(l.coords) > 1])
        if not g.is_empty:
            rivieres_mm.append((t, g))
    eau_occupee = unary_union([g.buffer(t / 2 + 0.25, quad_segs=2) for t, g in rivieres_mm
                               if t > TRAIT_COUPE_MM])
    shapely.prepare(eau_occupee)

    # --- lignes de partage des eaux
    print("Partage des eaux…")
    partage_versants, partage_bassins = lignes_partage(bassins, lambda g: geo_mm(g, surface=True), terre_libre)

    # --- noms des mers (réserve dans le bleu)
    print("Noms…")
    occupe = []
    mesure_mer = E.mesureur(*MER_POLICE)
    rayon_calotte = calotte.bounds[2]

    def parallele_mm(lon, lat):
        lons = np.arange(lon - 60, lon + 60, 0.05)
        x, y = geo_vers_aeqd.transform(lons, np.full_like(lons, lat))
        ok = np.hypot(x, y) < rayon_calotte
        xs, ys = aeqd_vers_nsper.transform(x[ok], y[ok])
        return vers_mm(LineString(np.column_stack([xs, ys])))

    def point_mm(lon, lat):
        return vers_mm(projeter(projeter(Point(lon, lat), geo_vers_aeqd), aeqd_vers_nsper))

    mer_libre = mer_seule.difference(glace_mm).buffer(-0.8)
    mers_svg, defs = [], []
    for k, (nom, lon, lat, t0, *angle) in enumerate(MERS):
        pt = point_mm(lon, lat)
        if not globe_mm.contains(pt):
            print(f"   ({nom} hors de la carte)")
            continue
        place = None
        for t in (t0, 0.9 * t0, 0.8 * t0, 0.7 * t0):
            L = mesure_mer(nom) * t + ESPACEMENT_MER * t * (len(nom) - 1)
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
                 f'letter-spacing="{ESPACEMENT_MER * t:.2f}" fill="#ffffff"')
        if base is None:
            mers_svg.append(f'<text {style} text-anchor="middle" x="{cx:.2f}" y="{cy + 0.36 * t:.2f}" '
                            f'transform="rotate({angle[0]} {cx:.2f} {cy:.2f})">{nom}</text>')
        else:
            ident = f"mer{k}"
            defs.append(f'<path id="{ident}" d="{E.d_lignes(base)}"/>')
            mers_svg.append(f'<text {style}><textPath xlink:href="#{ident}">{nom}</textPath></text>')
    print(f"   {len(mers_svg)} noms de mers")

    # --- légende, dans le blanc du ciel en haut à gauche : partage des eaux (noir),
    # rivières selon leur débit (bleu)
    legende_svg, legende_eaux_svg = [], []
    x0, y0, pas = MARGE + 6, MARGE + 14, 8.5

    def texte_leg(x, y, texte, gras=False):
        style = 'font-weight="bold" font-stretch="condensed" font-size="7"' if gras else 'font-size="4.5"'
        return f'<text x="{x:.2f}" y="{y:.2f}" font-family="Helvetica Neue" {style} fill="{{c}}">{texte}</text>'

    legende_svg.append(texte_leg(x0, y0, "Lignes de partage des eaux", gras=True))
    for i, (texte, ep, tirets, cap) in enumerate([
            ("entre les versants des mers", TRAIT_VERSANT_MM, TIRETS_VERSANT_MM, "butt"),
            ("entre les grands bassins fluviaux", TRAIT_BASSIN_MM, TIRETS_BASSIN_MM, "round")]):
        y = y0 + (i + 1) * pas
        legende_svg.append(f'<path d="M{x0:.2f},{y - 1.6:.2f}h20" stroke="{{c}}" stroke-width="{ep}" '
                           f'stroke-dasharray="{tirets[0]} {tirets[1]}" stroke-linecap="{cap}" fill="none"/>'
                           + texte_leg(x0 + 25, y, texte))
    y1 = y0 + 3.6 * pas
    legende_svg.append(texte_leg(x0, y1, "Rivières selon leur débit moyen", gras=True))
    for i, debit in enumerate((10, 500, 8000)):
        y = y1 + (i + 1) * pas
        ep = float(np.clip(FACTEUR_RIVIERE * debit ** 0.25, *TRAIT_RIVIERE_MM))
        legende_eaux_svg.append(f'<path d="M{x0:.2f},{y - 1.6:.2f}h20" stroke="{{c}}" stroke-width="{ep:.2f}" '
                                f'stroke-linecap="round" fill="none"/>')
        legende_svg.append(texte_leg(x0 + 25, y, f"{debit:,} m³/s".replace(",", " ")))
    occupe.append(box(x0 - 2, y0 - 8, x0 + 110, y1 + 3 * pas + 3))
    if globe_mm.intersects(occupe[-1]):
        print("   (attention : la légende touche la carte)")

    # --- noms des rivières, en bleu, le long de leur cours
    mesure_riv = E.mesureur(*RIVIERE_POLICE)
    ne = gpd.read_file(E.DATA / "ne_10m_rivers_lake_centerlines" / "ne_10m_rivers_lake_centerlines.shp")
    ne = ne[(ne.scalerank <= RANG_NOMS) & ne.name_fr.notna() & (ne.featurecla != "Canal")]
    terre_texte = terre_libre.buffer(-0.3)
    shapely.prepare(terre_texte)
    candidats = []
    for nom_ne, g in ne.groupby("name_fr"):
        nom = NOMS_RIVIERES.get(nom_ne, nom_ne)
        if nom is None or nom.lower().startswith("canal"):
            continue
        cours = geo_mm(shapely.line_merge(unary_union(list(g.geometry)))).intersection(globe_mm)
        parts = lignes_de(cours)
        if not parts:
            continue
        candidats.append((g.scalerank.min(), -cours.length, nom, max(parts, key=lambda l: l.length)))
    rivieres_noms_svg, noms_rivieres_e, n_places = [], [], 0
    for rang, _, nom, cours in sorted(candidats):
        t = next(v for k, v in sorted(RIVIERE_MM.items()) if rang <= k)
        blanc = next(v for k, v in sorted(ECART_NOM_MM.items()) if rang <= k)
        L = mesure_riv(nom) * t + ESPACEMENT_RIVIERE * t * (len(nom) - 1)
        lisse = LineString(chaikin(np.asarray(cours.simplify(1.0).coords), 3))
        if lisse.length < L + 4:
            continue
        positions = np.arange(L / 2 + 2, lisse.length - L / 2 - 2, 1.5)
        positions = positions[np.argsort(np.abs(positions - lisse.length / 2))]
        choix = None
        for s in positions:
            seg = shapely.ops.substring(lisse, s - L / 2, s + L / 2)
            c = np.asarray(seg.coords)
            if c[-1, 0] < c[0, 0]:
                seg = seg.reverse()   # lisible de gauche à droite
            seg_brut = seg
            seg, ecart_max = arc(seg)
            if ecart_max > ECART_ARC_MM:
                continue   # la rivière serpente trop pour que le nom la suive
            if np.hypot(*np.subtract(seg.coords[-1], seg.coords[0])) < COURBURE_MAX * seg.length:
                continue   # arc trop courbé : lettres en éventail
            for cote in (-1, 1):       # au-dessus (y décroissant), puis en dessous
                ecart = blanc + (0 if cote < 0 else 0.72 * t)
                # ligne de base prolongée : du côté concave, le décalage la raccourcit
                base = arc(seg_brut, prolonge=L / 2)[0].offset_curve(cote * ecart)
                if not isinstance(base, LineString):
                    continue
                e = seg.offset_curve(cote * (blanc + 0.36 * t)).buffer(0.42 * t, cap_style="flat")
                if (terre_texte.contains(e) and not eau_occupee.intersects(e)
                        and not any(e.intersects(o) for o in occupe)):
                    choix = (base, e)
                    break
            if choix:
                break
        if not choix:
            continue
        base, e = choix
        occupe.append(e)
        noms_rivieres_e.append(e)
        ident = f"riv{n_places}"
        n_places += 1
        defs.append(f'<path id="{ident}" d="{E.d_lignes(base)}"/>')
        rivieres_noms_svg.append(
            f'<text font-family="Helvetica Neue" font-weight="500" font-style="italic" font-size="{t:.2f}" '
            f'letter-spacing="{ESPACEMENT_RIVIERE * t:.2f}" text-anchor="middle" fill="{{c}}">'
            f'<textPath xlink:href="#{ident}" startOffset="50%">{nom}</textPath></text>')
    print(f"   {n_places} noms de rivières (sur {len(candidats)})")
    if noms_rivieres_e:
        halo = unary_union(noms_rivieres_e).buffer(0.3)
        rivieres_mm = [(t, g if t > TRAIT_COUPE_MM else g.difference(halo)) for t, g in rivieres_mm]

    # --- canevas, interrompu autour des noms et de la légende
    lignes_geo = [LineString([(lon, lat) for lat in np.arange(-10, (90 if lon % 30 == 0 else 80) + 0.01, 0.25)])
                  for lon in range(-180, 180, E.PAS_GRATICULE)]
    lignes_geo += [LineString([(lon, lat) for lon in np.arange(-180, 180.01, 0.25)])
                   for lat in range(E.PAS_GRATICULE, 90, E.PAS_GRATICULE)]
    graticule_mm = unary_union([geo_mm(l) for l in lignes_geo]).intersection(globe_mm)
    graticule_mm = graticule_mm.difference(unary_union(occupe).buffer(E.BLANC_AUTOUR_NOMS_MM))
    # les lignes de partage s'interrompent aussi autour des noms des rivières
    blanc = unary_union(occupe).buffer(E.BLANC_AUTOUR_NOMS_MM)
    partage_versants = partage_versants.difference(blanc)
    partage_bassins = partage_bassins.difference(blanc)

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

    defs_svg = f'<defs>{"".join(defs)}</defs>'
    contenus = {
        "terre": plein(terres_mm, "{c}"),
        "relief": f'<path d="{trame_d}" fill="{{c}}" stroke="none"/>',
        "eaux": defs_svg + plein(mer_mm, "{c}") + "".join(traits(g, t, "{c}") for t, g in rivieres_mm)
        + "".join(mers_svg) + "".join(rivieres_noms_svg) + "".join(legende_eaux_svg),
        "noir": traits(graticule_mm, E.TRAIT_GRATICULE_MM, "{c}")
        + traits(partage_bassins, TRAIT_BASSIN_MM, "{c}", TIRETS_BASSIN_MM)
        + traits(partage_versants, TRAIT_VERSANT_MM, "{c}", TIRETS_VERSANT_MM, cap="butt")
        + "".join(legende_svg),
    }
    noms = [("calque_1_terre", "terre", "1/4 — TERRE (ocre clair)"),
            ("calque_2_relief", "relief", "2/4 — RELIEF (marron foncé, trame)"),
            ("calque_3_eaux", "eaux", "3/4 — MERS, LACS ET RIVIÈRES (bleu)"),
            ("calque_4_noir", "noir", "4/4 — PARTAGE DES EAUX ET CANEVAS (noir)")]

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
    apercu.append(("reperes", "repères", E.reperes("#000")))
    ecrire("europe_eaux_apercu", E.svg(apercu, "Les eaux de l'Europe — aperçu"))

    for nom, cle, lab in noms:
        c = [("fond", "fond", f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#ffffff"/>'),
             (cle, lab, contenus[cle].replace("{c}", "#000000")),
             ("reperes", "repères", E.reperes("#000") + legende_calque(lab, "#000"))]
        ecrire(nom, E.svg(c, f"Les eaux de l'Europe — {lab}"))


if __name__ == "__main__":
    main()
