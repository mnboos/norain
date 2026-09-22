"""Bounded GPX interchange and distance-based timing for imported paths."""

import math
from bisect import bisect_right
from itertools import pairwise
from xml.etree import ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .geo import haversine_m

MAX_GPX_BYTES = 10 * 1024 * 1024
MAX_TRACK_POINTS = 100_000
MAX_DURATION_SECONDS = 16 * 24 * 3600
GPX_NS = "http://www.topografix.com/GPX/1/1"


def validate_track(points):
    if not 2 <= len(points) <= MAX_TRACK_POINTS:
        raise ValueError("Eine Strecke braucht 2 bis 100000 Punkte.")
    result = []
    for point in points:
        if len(point) not in (2, 3):
            raise ValueError("Ein Punkt muss Längengrad, Breitengrad und optional Höhe enthalten.")
        values = [float(v) for v in point]
        if not all(math.isfinite(v) for v in values) or not (-180 <= values[0] <= 180 and -90 <= values[1] <= 90):
            raise ValueError("Die Datei enthält ungültige Koordinaten.")
        result.append(values)
    if not any(p[:2] != result[0][:2] for p in result[1:]):
        raise ValueError("Die Strecke braucht mindestens zwei unterschiedliche Positionen.")
    return result


def distances(points):
    cumulative = [0.0]
    for a, b in pairwise(points):
        cumulative.append(cumulative[-1] + haversine_m(a[0], a[1], b[0], b[1]))
    return cumulative


def parse_gpx(raw):
    if len(raw) > MAX_GPX_BYTES:
        raise ValueError("Die GPX-Datei darf höchstens 10 MiB gross sein.")
    try:
        doc = fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ValueError("Die Datei ist kein gültiges GPX-XML.") from exc
    namespace = doc.tag.partition("}")[0][1:] if doc.tag.startswith("{") else ""
    if namespace not in ("", "http://www.topografix.com/GPX/1/0", GPX_NS):
        raise ValueError("Unbekanntes GPX-Format.")
    prefix = "{" + namespace + "}" if namespace else ""
    if doc.tag != prefix + "gpx" or doc.get("version") not in ("1.0", "1.1"):
        raise ValueError("Bitte eine GPX-Datei der Version 1.0 oder 1.1 wählen.")
    count = sum(1 for e in doc.iter() if e.tag in (prefix + "trkpt", prefix + "rtept", prefix + "wpt"))
    if count > MAX_TRACK_POINTS:
        raise ValueError("Die GPX-Datei darf höchstens 100000 Punkte enthalten.")
    paths = []
    for element in doc:
        if element.tag not in (prefix + "trk", prefix + "rte"):
            continue
        name = (element.findtext(prefix + "name") or "Importierte Strecke").strip()[:200]
        segments = element.findall(prefix + "trkseg") if element.tag == prefix + "trk" else [element]
        for index, segment in enumerate(segments):
            points = []
            for point in segment:
                if point.tag not in (prefix + "trkpt", prefix + "rtept"):
                    continue
                try:
                    coords = [float(point.attrib["lon"]), float(point.attrib["lat"])]
                    elevation = point.findtext(prefix + "ele")
                    if elevation is not None:
                        coords.append(float(elevation))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Die Datei enthält ungültige Koordinaten oder Höhen.") from exc
                points.append(coords)
            if not points:
                continue
            points = validate_track(points)
            label = name if len(segments) == 1 else f"{name} – Abschnitt {index + 1}"
            paths.append(
                {
                    "name": label[:200],
                    "coordinates": points,
                    "distance_m": distances(points)[-1],
                    "routing_points": guided_points(points),
                }
            )
    if not paths:
        raise ValueError("Keine Strecke gefunden. Einzelne Wegpunkte reichen nicht aus.")
    return paths


def guided_points(points, limit=17):
    """Split at the largest deviation repeatedly, with stable ordering and endpoints."""
    selected = {0, len(points) - 1}
    while len(selected) < min(limit, len(points)):
        best = None
        indices = sorted(selected)
        for start, end in pairwise(indices):
            a, b = points[start], points[end]
            scale = math.cos(math.radians((a[1] + b[1]) / 2))
            dx, dy = (b[0] - a[0]) * scale, b[1] - a[1]
            denom = dx * dx + dy * dy
            for i in range(start + 1, end):
                x, y = (points[i][0] - a[0]) * scale, points[i][1] - a[1]
                t = min(1, max(0, (x * dx + y * dy) / denom)) if denom else 0
                error = (x - t * dx) ** 2 + (y - t * dy) ** 2
                if best is None or error > best[0]:
                    best = (error, i)
        if best is None or best[0] <= 1e-16:
            break
        selected.add(best[1])
    return [points[i][:2] for i in sorted(selected)]


def exact_geometry(points, duration_seconds, interval_seconds=300):
    points = validate_track(points)
    if not math.isfinite(duration_seconds) or not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("Die Fahrzeit muss zwischen einer Sekunde und 16 Tagen liegen.")
    cumulative = distances(points)
    total = cumulative[-1]
    times = [d / total * duration_seconds for d in cumulative]
    # Add sample positions to the line, retaining every imported vertex in order.
    targets = [*range(0, math.ceil(duration_seconds), max(60, interval_seconds)), duration_seconds]
    vertices = [(t, i, p[:2]) for i, (t, p) in enumerate(zip(times, points, strict=True))]
    for t in targets:
        i = min(max(0, bisect_right(times, t) - 1), len(points) - 2)
        span = times[i + 1] - times[i]
        fraction = (t - times[i]) / span if span else 0
        a, b = points[i], points[i + 1]
        delta_lon = (b[0] - a[0] + 180) % 360 - 180
        lon = (a[0] + fraction * delta_lon + 180) % 360 - 180
        vertices.append((t, len(points), [lon, a[1] + fraction * (b[1] - a[1])]))
    vertices.sort(key=lambda v: (v[0], v[1]))
    line, vertex_times, samples = [], [], []
    target_set = set(targets)
    sampled = set()
    for t, _, position in vertices:
        line.append(position)
        vertex_times.append(t)
        if t in target_set and t not in sampled:
            lon, lat = position
            samples.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "lat_r": round(lat, 2),
                    "lon_r": round(lon, 2),
                    "elapsed_s": t,
                    "idx": len(line) - 1,
                }
            )
            sampled.add(t)
    return {
        "polyline": line,
        "vertex_times": vertex_times,
        "sample_points": samples,
        "total_seconds": round(duration_seconds),
        "total_distance_m": total,
    }


def serialize_gpx(name, points):
    points = validate_track(points)
    doc = ET.Element("gpx", {"xmlns": GPX_NS, "version": "1.1", "creator": "NoRain"})
    track = ET.SubElement(doc, "trk")
    # XML 1.0 rejects control characters even when text escaping is used.
    clean_name = "".join(
        c
        for c in name[:200]
        if c in "\t\n\r" or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF
    )
    ET.SubElement(track, "name").text = clean_name or "NoRain"
    segment = ET.SubElement(track, "trkseg")
    for point in points:
        node = ET.SubElement(segment, "trkpt", {"lat": str(point[1]), "lon": str(point[0])})
        if len(point) == 3:
            ET.SubElement(node, "ele").text = str(point[2])
    return ET.tostring(doc, encoding="utf-8", xml_declaration=True)
