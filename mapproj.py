"""
mapproj.py — Albers USA composite conic projection, implemented from the
standard cartographic formulas (no mapping library). Projects lon/lat into
a fixed SVG coordinate space (viewBox 0 0 960 600) so that state boundaries,
site markers, and patient home locations all land in one consistent frame,
computed once at build time.

The composite is the conventional "Albers USA" layout: continental US on a
standard Albers equal-area conic (parallels 29.5N/45.5N, centered -96W/38N),
with Alaska and Hawaii re-projected on their own conics and inset into the
bottom-left, at the scale/placement long established by US Census/D3 Albers
USA maps. This is public cartographic convention, not any single library's
proprietary code.

Since every location in this app's data carries a known US state, routing a
point to the right sub-projection is done by state name rather than by
re-deriving D3's clip-box heuristic — simpler and exact for a finite dataset.
"""
import math

WIDTH, HEIGHT = 960, 600
SCALE = 1070.0
TRANSLATE = (487.5, 305.0)


class Conic:
    """Albers equal-area conic projection to screen space (y increases
    downward, i.e. north is up on screen, matching normal map orientation)."""

    def __init__(self, rotate_lon, center_lon, center_lat, parallel1, parallel2):
        self.rotate_lon = math.radians(rotate_lon)
        self.center_lon = math.radians(center_lon)
        phi1 = math.radians(parallel1)
        phi2 = math.radians(parallel2)
        phi0 = math.radians(center_lat)
        self.n = (math.sin(phi1) + math.sin(phi2)) / 2.0
        self.C = math.cos(phi1) ** 2 + 2 * self.n * math.sin(phi1)
        self.rho0 = self._rho(phi0)
        self.scale = SCALE
        self.tx, self.ty = 0.0, 0.0

    def _rho(self, phi):
        val = self.C - 2 * self.n * math.sin(phi)
        return math.sqrt(max(val, 0)) / self.n

    def set_translate(self, tx, ty):
        self.tx, self.ty = tx, ty

    def set_scale(self, scale):
        self.scale = scale

    def project(self, lon_deg, lat_deg):
        lon = math.radians(lon_deg) + self.rotate_lon
        lat = math.radians(lat_deg)
        theta = self.n * (lon - self.center_lon)
        rho = self._rho(lat)
        x = rho * math.sin(theta)
        y = self.rho0 - rho * math.cos(theta)
        # Flip y: in this math convention north (+lat) yields larger y (up in
        # a y-up Cartesian plane); SVG is y-down, so negate to keep north at
        # the top of the screen.
        return (x * self.scale + self.tx, -y * self.scale + self.ty)


def _make_layout():
    lower48 = Conic(rotate_lon=96, center_lon=0, center_lat=38, parallel1=29.5, parallel2=45.5)
    alaska = Conic(rotate_lon=154, center_lon=-2, center_lat=58.5, parallel1=55, parallel2=65)
    hawaii = Conic(rotate_lon=157, center_lon=-3, center_lat=19.9, parallel1=8, parallel2=18)

    k = SCALE
    x, y = TRANSLATE

    lower48.set_scale(k)
    lower48.set_translate(x, y)

    # Alaska: shrunk to 0.35x scale, inset bottom-left of the main map.
    alaska.set_scale(k * 0.35)
    alaska.set_translate(x - 0.307 * k, y + 0.201 * k)

    # Hawaii: full scale, inset just right of the Alaska box.
    hawaii.set_scale(k)
    hawaii.set_translate(x - 0.205 * k, y + 0.212 * k)

    return lower48, alaska, hawaii


LOWER48, ALASKA, HAWAII = _make_layout()

_REGION_PROJECTIONS = {"Alaska": ALASKA, "Hawaii": HAWAII}


def project(lon_deg, lat_deg, state_name=None):
    """Project a lon/lat pair into the composite Albers USA SVG frame.

    state_name, when given, routes Alaska/Hawaii to their inset conics;
    everything else (including unspecified) uses the continental conic.
    Puerto Rico and other non-state territories are not part of this map.
    """
    proj = _REGION_PROJECTIONS.get(state_name, LOWER48)
    return proj.project(lon_deg, lat_deg)


def local_pixels_per_degree_lat(lat_deg, lon_deg=-96.0):
    """Approximate local scale (SVG px per degree latitude) at a point, used
    only to draw the Map view's schematic 25/50/100-mile distance rings —
    not for any distance figure shown as text (those use haversine miles)."""
    x1, y1 = LOWER48.project(lon_deg, lat_deg - 0.05)
    x2, y2 = LOWER48.project(lon_deg, lat_deg + 0.05)
    d = math.hypot(x2 - x1, y2 - y1)
    return d / 0.10


def ring_radius_px(lat_deg, lon_deg, miles):
    px_per_deg_lat = local_pixels_per_degree_lat(lat_deg, lon_deg)
    px_per_mile = px_per_deg_lat / 69.0  # ~69 miles per degree of latitude
    return miles * px_per_mile


def polygon_to_path_d(coordinates, state_name=None):
    """GeoJSON Polygon coordinates -> SVG path 'd', one subpath per ring."""
    parts = []
    for ring in coordinates:
        pts = [project(lon, lat, state_name) for lon, lat in ring]
        d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"
        parts.append(d)
    return " ".join(parts)


def geometry_to_path_d(geometry, state_name=None):
    if geometry["type"] == "Polygon":
        return polygon_to_path_d(geometry["coordinates"], state_name)
    if geometry["type"] == "MultiPolygon":
        return " ".join(polygon_to_path_d(poly, state_name) for poly in geometry["coordinates"])
    raise ValueError(f"Unsupported geometry type: {geometry['type']}")
