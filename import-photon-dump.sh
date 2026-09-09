#!/bin/bash

set -euxo pipefail
    
zstd --stdout -d /photon-dump.jsonl.zst | java -Xmx14g -jar photon.jar import -import-file -