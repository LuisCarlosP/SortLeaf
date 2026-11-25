"""Build the Costa Rica recycling extract. Requires pyosmium only at build time.

Usage: python tools/build_recycling_catalog.py costa-rica.osm.pbf costa-rica.poly
Input: https://download.geofabrik.de/central-america/costa-rica.html
"""
import argparse
import json
from pathlib import Path

import osmium


def build(pbf: Path, polygon: Path) -> dict:
    extract = osmium.FileProcessor(pbf)
    elements = []
    relations = set()
    resolved = set()
    for obj in extract.with_locations().with_areas().with_filter(osmium.filter.KeyFilter("amenity", "recycling_type")):
        if obj.tags.get("amenity") != "recycling" and "recycling_type" not in obj.tags:
            continue
        tags = {k: v for k, v in obj.tags if k.startswith(("recycling:", "addr:", "contact:"))
                or k in ("amenity", "recycling_type", "name", "operator", "phone", "website", "opening_hours")}
        if obj.is_node():
            elements.append(dict(type="node", id=obj.id, lat=obj.location.lat, lon=obj.location.lon, tags=tags))
            continue
        if obj.is_relation():
            relations.add(obj.id)
            continue
        if obj.is_way():
            nodes, osm_type, osm_id = obj.nodes, "way", obj.id
        elif obj.is_area() and not obj.from_way():
            nodes = (node for ring in obj.outer_rings() for node in ring)
            osm_type, osm_id = "relation", obj.orig_id()
            resolved.add(osm_id)
        else:
            continue
        coordinates = [(n.location.lat, n.location.lon) for n in nodes]
        lats, lons = zip(*coordinates)
        center = dict(lat=round((min(lats)+max(lats))/2, 7), lon=round((min(lons)+max(lons))/2, 7))
        elements.append(dict(type=osm_type, id=osm_id, center=center, tags=tags))
    if relations - resolved:
        raise ValueError(f"Unresolved recycling relations: {relations - resolved}")
    lines = polygon.read_text().strip().splitlines()
    if lines[1] != "1" or lines[-2:] != ["END", "END"]:
        raise ValueError("Expected the single exterior ring of the Costa Rica extract")
    return {
        "copyright": "© OpenStreetMap contributors, ODbL 1.0",
        "source": "https://download.geofabrik.de/central-america/costa-rica.html",
        "license": "https://opendatacommons.org/licenses/odbl/1-0/",
        "as_of": extract.header.get("osmosis_replication_timestamp"),
        "boundary": [[float(v) for v in line.split()] for line in lines[2:-2]],
        "elements": sorted(elements, key=lambda e: (e["type"], e["id"])),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("polygon", type=Path)
    args = parser.parse_args()
    catalog = build(args.pbf, args.polygon)
    output = Path(__file__).resolve().parents[1] / "data" / "costa-rica-recycling.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(catalog['elements'])} recycling locations, {catalog['as_of']}")
