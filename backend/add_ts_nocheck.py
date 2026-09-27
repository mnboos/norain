"""Add @ts-nocheck to generated API model files that don't already have it.

Workaround for openapi-generator v7.23.0 producing dual camelCase/snake_case
instanceOf checks that create union types TS 6 can't index into.
"""

import glob
import os
import time

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "packages", "api", "models")
WRITE_ATTEMPTS = 10


def _prepend_header(filepath, content):
    # On Windows the freshly generated files are often still held open by the IDE's
    # indexer or the virus scanner, and opening them for writing fails for a moment
    # (EINVAL or EACCES). Retry briefly; a lasting failure still raises.
    for attempt in range(WRITE_ATTEMPTS):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("// @ts-nocheck\n")
                f.write(content)
            return
        except OSError:
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


count = 0
for filepath in glob.glob(os.path.join(MODELS_DIR, "*.ts")):
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    if content.startswith("// @ts-nocheck"):
        continue

    _prepend_header(filepath, content)
    count += 1
    print(f"  Added @ts-nocheck to {os.path.basename(filepath)}")

if count == 0:
    print("  All model files already have @ts-nocheck")
