#!/bin/bash
#
# Cut an OSM extract down to what the bike profiles use, so the graph builds faster and in
# less memory. The entrypoint runs it before every build, and `just osm-filter-many-raw-pbf-into-one` runs it on
# local files, so every graph is built from the same kind of data.
#
# osmium also keeps every node the kept ways use (with its tags, so barriers stay) and the
# members of the kept relations.
#   highways bikes might use: motorways are in import.osm.ignored_highways, the rest are not
#                             built yet or are gone
#   ferries and piers:        GraphHopper's bike access takes both
#   cycle-route relations:    bike.json's priority reads the bike network from them
# Turn restrictions are left out: no profile in graphhopper-config.yaml uses turn_costs.
set -euo pipefail

out="${1:?Usage: filter-osm.sh OUT.osm.pbf IN.osm.pbf [IN.osm.pbf ...]}"
shift
[ $# -gt 0 ] || { echo "Usage: filter-osm.sh OUT.osm.pbf IN.osm.pbf [IN.osm.pbf ...]" >&2; exit 1; }

bike_filter() {
    osmium tags-filter "$1" \
        'w/highway!=motorway,motorway_link,proposed,construction,abandoned,razed' \
        w/route=ferry \
        w/man_made=pier \
        'r/route=bicycle,mtb' \
        --overwrite --output-format pbf -o "$2"
}

# Write to a temporary name first: a failed run must never leave a file that looks ready.
if [ $# -eq 1 ]; then
    bike_filter "$1" "$out.tmp"
else
    # Several extracts (e.g. one per country) become one: filter each, then merge the small
    # results. Objects on a shared border are in both files; merge keeps them once.
    parts=$(mktemp -d "$(dirname "$out")/.filter-XXXXXX")
    trap 'rm -rf -- "$parts"' EXIT
    i=0
    for extract in "$@"; do
        echo "Filtering $extract"
        bike_filter "$extract" "$parts/$i.osm.pbf"
        i=$((i + 1))
    done
    echo "Merging ${#} filtered extracts"
    osmium merge "$parts"/*.osm.pbf --overwrite --output-format pbf -o "$out.tmp"
fi
mv "$out.tmp" "$out"
