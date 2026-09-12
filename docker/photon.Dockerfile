FROM eclipse-temurin:24-jre

RUN apt update -y && \
    apt install -y --no-install-recommends ca-certificates pbzip2 wget zstd && \
    rm -rf /var/lib/apt/lists/*

#WORKDIR /photon

ADD https://github.com/komoot/photon/releases/download/1.0.1/photon-1.0.1.jar photon.jar

COPY docker/photon-entrypoint.sh /entrypoint.sh
COPY import-photon-dump.sh import-photon-dump.sh
RUN chmod +x /entrypoint.sh

# The entrypoint downloads the index named by $PHOTON_INDEX_URL (if absent) then starts photon.
ENTRYPOINT ["/entrypoint.sh"]