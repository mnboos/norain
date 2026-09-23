#!/bin/bash
#
# Extract the points of interest the journey planner uses (water, toilets, shelters, lodging,
# ...) from raw OSM extracts into one GeoJSON-sequence file, which `manage.py import_pois`
# loads into PostGIS.
#
# The bike filter (graphhopper-filter-osm.sh) cannot be used for this: it keeps only ways and
# their nodes, and a toilet or a water tap is a standalone node or a small area. So this reads
# the raw extract, which the graphhopper entrypoint keeps in /osm_data.
#
# The tag list must cover every tag in backend/core/pois.py POI_RULES; a test checks that.
# Ways and relations are kept for areas (a campsite is usually a polygon); the import takes a
# point on each surface.
set -euo pipefail

out="${1:?Usage: osm-extract-pois.sh OUT.geojsonseq IN.osm.pbf [IN.osm.pbf ...]}"
shift
[ $# -gt 0 ] || { echo "Usage: osm-extract-pois.sh OUT.geojsonseq IN.osm.pbf [IN.osm.pbf ...]" >&2; exit 1; }

poi_filter() {
    osmium tags-filter "$1" \
        nwr/amenity=toilets,bbq,drinking_water,vending_machine,shelter,bicycle_repair_station,cafe,restaurant,fast_food,biergarten,charging_station,fountain,water_point \
        nwr/shop=bicycle,supermarket,convenience,bakery \
        nwr/man_made=water_tap \
        nwr/natural=spring \
        nwr/railway=station,halt \
        nwr/tourism=hotel,hostel,guest_house,camp_site,alpine_hut,wilderness_hut \
        --overwrite --output-format pbf -o "$2"
}

parts=$(mktemp -d "$(dirname "$out")/.pois-XXXXXX")
trap 'rm -rf -- "$parts"' EXIT
i=0
for extract in "$@"; do
    echo "Extracting POIs from $extract"
    poi_filter "$extract" "$parts/$i.osm.pbf"
    i=$((i + 1))
done
if [ $# -gt 1 ]; then
    # Objects on a shared border are in both files; merge keeps them once.
    osmium merge "$parts"/[0-9]*.osm.pbf --overwrite --output-format pbf -o "$parts/all.osm.pbf"
else
    mv "$parts/0.osm.pbf" "$parts/all.osm.pbf"
fi

# type_id gives each feature an id like n123 / w456, the Poi.osm_ref. Write to a temporary
# name first: a failed run must never leave a file that looks ready.
osmium export "$parts/all.osm.pbf" -f geojsonseq --add-unique-id=type_id \
    -x print_record_separator=false --overwrite -o "$out.tmp"
mv "$out.tmp" "$out"
echo "Wrote $(wc -l < "$out") features to $out"
