#!/usr/bin/env bash
#
# Install what scripts/graphhopper-host-build.sh needs: Java 25 (the image's), osmium-tool and
# python3 >= 3.11. Skips what is already there, so it is safe to run again. macOS uses Homebrew;
# Linux uses apt or dnf. The jar itself is not installed here: the build copies it out of the image.
set -euo pipefail

have_java25() {
    local java
    if [ -x /usr/libexec/java_home ]; then
        java="$(/usr/libexec/java_home -v 25 2>/dev/null)/bin/java" || return 1
    else
        java="${JAVA_HOME:+$JAVA_HOME/bin/}java"
    fi
    [ "$("$java" -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java\.specification\.version = //p')" = 25 ]
}
have_python() { command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; }

missing=()
have_java25 || missing+=(java)
command -v osmium >/dev/null || missing+=(osmium)
have_python || missing+=(python)
if [ ${#missing[@]} -eq 0 ]; then
    echo "Java 25, osmium and python3 >= 3.11 are installed."
    exit 0
fi
echo "Installing: ${missing[*]}"

wants() { [[ " ${missing[*]} " == *" $1 "* ]]; }

case "$(uname -s)" in
    Darwin)
        command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh" >&2; exit 1; }
        # The Temurin cask is a .pkg: macOS asks for the admin password.
        if wants java; then brew install --cask temurin@25; fi
        if wants osmium; then brew install osmium-tool; fi
        if wants python; then brew install python3; fi
        ;;
    Linux)
        if command -v apt-get >/dev/null; then
            packages=()
            wants osmium && packages+=(osmium-tool)
            wants python && packages+=(python3)
            if wants java; then
                # Debian/Ubuntu ship OpenJDK 25 only in recent releases; otherwise Adoptium's
                # repository has Temurin 25.
                if apt-cache show openjdk-25-jdk-headless >/dev/null 2>&1; then
                    packages+=(openjdk-25-jdk-headless)
                else
                    sudo apt-get update
                    sudo apt-get install -y --no-install-recommends wget gpg ca-certificates
                    wget -qO- https://packages.adoptium.net/artifactory/api/gpg/key/public \
                        | sudo gpg --dearmor --yes -o /usr/share/keyrings/adoptium.gpg
                    codename=$(. /etc/os-release && echo "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")
                    echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $codename main" \
                        | sudo tee /etc/apt/sources.list.d/adoptium.list >/dev/null
                    packages+=(temurin-25-jdk)
                fi
            fi
            sudo apt-get update
            sudo apt-get install -y --no-install-recommends "${packages[@]}"
        elif command -v dnf >/dev/null; then
            packages=()
            wants osmium && packages+=(osmium-tool)
            wants python && packages+=(python3)
            wants java && packages+=(java-25-openjdk-headless)
            sudo dnf install -y "${packages[@]}"
        else
            echo "No apt or dnf. Install by hand: Java 25, osmium-tool, python3 >= 3.11." >&2
            exit 1
        fi
        ;;
    *)
        echo "Unsupported system $(uname -s). Build in the container: just build-graphhopper-graph-from FILE" >&2
        exit 1
        ;;
esac

# Several JDKs side by side: the build picks 25 itself on macOS; on Linux set JAVA_HOME if the
# default java is another version.
have_java25 || echo "Java 25 is installed but not the default java: set JAVA_HOME to it." >&2
have_python || { echo "python3 is still older than 3.11." >&2; exit 1; }
command -v osmium >/dev/null || { echo "osmium is still missing." >&2; exit 1; }
echo "Done. Build with: just build-graphhopper-graph-host FILE"
