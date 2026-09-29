"""Add @ts-nocheck to generated API model files that don't already have it.

Workaround for openapi-generator v7.23.0 producing dual camelCase/snake_case
instanceOf checks that create union types TS 6 can't index into.

Also turns the generator's file manifest (.openapi-generator/FILES) into LF: the
generator writes it with the platform's line separator, CRLF on Windows, while
everything else it writes is LF. Every file here is read and written with
newline="", so Python's text mode never turns "\n" into "\r\n" on Windows.
"""

import glob
import os
import time

API_DIR = os.path.join(os.path.dirname(__file__), "..", "packages", "api")
MODELS_DIR = os.path.join(API_DIR, "models")
MANIFEST = os.path.join(API_DIR, ".openapi-generator", "FILES")
WRITE_ATTEMPTS = 10


def _read(filepath):
    with open(filepath, encoding="utf-8", newline="") as f:
        return f.read()


def _write(filepath, content):
    # On Windows the freshly generated files are often still held open by the IDE's
    # indexer or the virus scanner, and opening them for writing fails for a moment
    # (EINVAL or EACCES). Retry briefly; a lasting failure still raises.
    for attempt in range(WRITE_ATTEMPTS):
        try:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return
        except OSError:
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


count = 0
for filepath in glob.glob(os.path.join(MODELS_DIR, "*.ts")):
    content = _read(filepath)

    if content.startswith("// @ts-nocheck"):
        continue

    _write(filepath, "// @ts-nocheck\n" + content)
    count += 1
    print(f"  Added @ts-nocheck to {os.path.basename(filepath)}")

if count == 0:
    print("  All model files already have @ts-nocheck")

if os.path.exists(MANIFEST):
    manifest = _read(MANIFEST)
    if "\r\n" in manifest:
        _write(MANIFEST, manifest.replace("\r\n", "\n"))
        print("  Converted .openapi-generator/FILES to LF")
