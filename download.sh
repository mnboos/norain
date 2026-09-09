mkdir -p data data/otp

wget -O data/switzerland-latest.osm.pbf https://download.geofabrik.de/europe/switzerland-latest.osm.pbf
#wget -O data/photon-db-ch-latest.tar.bz2 https://download1.graphhopper.com/public/experimental/extracts/by-country-code/ch/photon-db-ch-latest.tar.bz2

# Swiss GTFS for public transport (OTP)
# Dataset: Fahrplan 2026 (GTFS 2020)
# https://data.opentransportdata.swiss/de/dataset/timetable-2026-gtfs2020/resource/8e267b6b-3b2c-4a65-b257-cd4a7ce76f3e
#
# OTP needs the GTFS as a ZIP — do NOT unzip it.
# Download the ZIP from the resource page above and save it as data/otp/gtfs.zip, then run this script.
# data/otp/ must contain both files before starting OTP:
#   - gtfs.zip                        (GTFS timetable, keep as ZIP)
#   - switzerland-latest.osm.pbf      (symlinked below)
ln -sf "$(pwd)/data/switzerland-latest.osm.pbf" data/otp/switzerland-latest.osm.pbf