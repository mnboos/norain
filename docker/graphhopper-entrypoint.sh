#!/bin/bash

OSM_DATA_FILE=$(basename $OSM_DATA_URL)
echo "Requested OSM data: ${OSM_DATA_FILE}"
OSM_DATA_FILE=/${OSM_DATA_DIR}/${OSM_DATA_FILE}

if [ ! -s $OSM_DATA_FILE ]; then
    echo "Downloading OSM data"

    USER_AGENT="job-graph"
    wget \
    --no-check-certificate \
		--user-agent="$USER_AGENT" \
		--show-progress \
		--progress=bar:force:noscroll \
		-O $OSM_DATA_FILE \
		$OSM_DATA_URL
	
fi

if [ -s $OSM_DATA_FILE ]; then
    echo "Start graphhopper"
	
	#java -jar graphhopper.jar import /config.yaml
	java -jar graphhopper.jar check /config.yaml
	# Heap scales with extract size; override with GRAPHHOPPER_HEAP (e.g. 16g) for larger areas.
	GRAPHHOPPER_HEAP="${GRAPHHOPPER_HEAP:-6g}"
    java -Xmx${GRAPHHOPPER_HEAP} -Xms${GRAPHHOPPER_HEAP} -Ddw.graphhopper.datareader.file=${OSM_DATA_FILE} -jar graphhopper.jar server /config.yaml
else
    echo "Could not start graphhopper, no OSM data was found."
	exit 1
fi
