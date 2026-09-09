# Start from the official PostGIS 16 image, as you wanted.
FROM postgis/postgis:17-3.5

# The base image runs as the 'postgres' user. To install software,
# we need to switch to the 'root' user temporarily.
USER root

# Run the package manager to update its lists and install osm2pgsql.
# The --no-install-recommends flag prevents installing unnecessary packages.
# We clean up the apt cache in the same layer to keep the image size down.
RUN apt-get update \
    && apt-get install -y --no-install-recommends osm2pgsql \
    && rm -rf /var/lib/apt/lists/*

# IMPORTANT: Switch back to the default postgres user so the database
# starts up correctly with the right permissions.
#USER postgres