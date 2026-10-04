"""Carte de l'Europe vue de l'espace, en 3 calques de sérigraphie.

Projection perspective verticale (vue satellite, « nsper ») : l'Europe remplit
la feuille et le haut montre l'horizon courbe du globe, au-delà du pôle Nord,
avec le papier blanc au-dessus.

Ordre d'impression (la plus claire d'abord) :
  1. terre  (ocre clair, façon carte d'école) : toutes les terres ; déborde de
     0,3 mm sous la mer (trapping)
  2. mer    (bleu) : mers, océans, grands lacs, et fleuves principaux imprimés
     par-dessus la terre
  3. noir : frontières, noms des pays et capitales, par-dessus le reste

Sorties dans sortie/ : un SVG et un PDF par calque (noir sur blanc),
plus un aperçu en couleur.
"""
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
import shapely.ops
from pyproj import Transformer
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

TRAIT_FRONTIERE_MM = 0.3
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
CAPITALE_MM = 3.6                # corps des noms de capitales
CAPITALE_POLICE = ("/System/Library/Fonts/HelveticaNeue.ttc", "Bold Italic")
RAYON_CAPITALE_MM = 0.9          # rond du symbole (trait 0,3 mm, point central plein)
NOMS_CAPITALES = {"Noursoultan": "Astana"}   # noms à corriger (Astana a repris son nom en 2022)
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
# Moldavie : inclinée le long du pays)
POSITIONS = {"Morocco": (-5.2, 34.2), "Algeria": (3.5, 35.4), "Tunisia": (9.4, 34.6),
             "Kazakhstan": (52.0, 48.8), "United Kingdom": (-1.6, 52.7), "Croatia": (17.6, 45.42),
             "Azerbaijan": (47.9, 40.4), "Armenia": (44.75, 40.1), "Montenegro": (18.3, 42.0, 0),
             "Spain": (-2.6, 39.9), "Moldova": (28.45, 47.05, 61)}

COULEURS = {"terre": "#e8d08c", "mer": "#3d6f9e", "noir": "#1a1a1a"}

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

    pays = gpd.read_file(DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp")
    pays = pays[pays.TYPE.isin(["Sovereign country", "Country", "Disputed", "Sovereignty"])
                & pays.intersects(zone)].copy()
    pays["geometry"] = pays.geometry.buffer(0).intersection(zone)
    return terres, lacs, fleuves, frontieres, pays, capitales


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

    return vers_mm, ech


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
    terres, lacs, fleuves, frontieres, pays, capitales = charger(theta)

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
    capitales_nsper = []   # (nom, code pays, point nsper)
    for _, v in capitales.iterrows():
        pt = projeter(v.geometry, geo_vers_aeqd)
        if calotte.contains(pt):
            capitales_nsper.append((NOMS_CAPITALES.get(v.NAME_FR, v.NAME_FR), v.ADM0_A3,
                                    projeter(pt, aeqd_vers_nsper)))
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

    vers_mm, ech = mise_en_page(horizon)
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
        for t in tailles:
            for d in (0, 0.6 * t, -0.6 * t, 1.2 * t, -1.2 * t):
                for mode in ordre:
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
    print(f"   {len(occupe) - len(capitales_mm)} noms de pays")

    # capitales : symbole, puis nom autour du point (est, ouest, diagonales, nord, sud)
    mesure_cap = mesureur(*CAPITALE_POLICE)
    capitales_svg = []
    for nom, pt in capitales_mm:
        x, y = pt.x, pt.y
        capitales_svg.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{RAYON_CAPITALE_MM}" fill="none" stroke="{{c}}" stroke-width="0.3"/>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.35" fill="{{c}}"/>')
        place = None
        for t in (CAPITALE_MM, 0.85 * CAPITALE_MM):
            L, g = mesure_cap(nom) * t, RAYON_CAPITALE_MM + 0.8
            h = 0.5 * t                       # demi-hauteur de l'emprise (capitales + soulignement)
            for gauche, centre in [(x + g, y), (x - g - L, y),
                                   (x + 0.7 * g, y - 0.7 * g - h), (x + 0.7 * g, y + 0.7 * g + h),
                                   (x - 0.7 * g - L, y - 0.7 * g - h), (x - 0.7 * g - L, y + 0.7 * g + h),
                                   (x - L / 2, y - g - h), (x - L / 2, y + g + h)]:
                e = box(gauche, centre - h, gauche + L, centre + h)
                if globe_mm.contains(e) and not any(e.intersects(o) for o in occupe):
                    place = (gauche, centre, t, L)
                    break
            if place:
                break
        if not place:
            print(f"   (pas de place pour la capitale {nom!r})")
            continue
        occupe.append(e)
        gauche, centre, t, L = place
        base = centre + 0.25 * t
        capitales_svg.append(
            f'<text x="{gauche:.2f}" y="{base:.2f}" font-family="{POLICE}" font-weight="bold" '
            f'font-style="italic" font-size="{t:.2f}" fill="{{c}}">{nom}</text>'
            f'<path d="M{gauche:.2f},{base + 0.15 * t:.2f}H{gauche + L:.2f}" stroke="{{c}}" '
            f'stroke-width="{0.07 * t:.2f}"/>')
    print(f"   {len(capitales_mm)} capitales")

    def plein(g, c):
        return f'<path d="{d_poly(g)}" fill="{c}" fill-rule="evenodd" stroke="none"/>'

    def legende(texte, c):
        return (f'<text x="{PAGE_L / 2}" y="{PAGE_H - MARGE / 2 + 1.5}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="4" text-anchor="middle" fill="{c}">{texte}</text>')

    def traits(g, ep, c):
        return (f'<path d="{d_lignes(g)}" fill="none" stroke="{c}" stroke-width="{ep}" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')

    contenus = {"terre": plein(terres_mm, "{c}"), "mer": plein(mer_mm, "{c}"),
                "noir": traits(frontieres_mm, TRAIT_FRONTIERE_MM, "{c}") + "".join(noms_svg)
                + "".join(capitales_svg)}
    noms = [("calque_1_terre", "terre", "1/3 — TERRE (ocre clair)"),
            ("calque_2_mer", "mer", "2/3 — MER ET FLEUVES (bleu)"),
            ("calque_3_noir", "noir", "3/3 — FRONTIÈRES ET NOMS (noir)")]

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
