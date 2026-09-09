"""Add @ts-nocheck to generated API model files that don't already have it.

Workaround for openapi-generator v7.23.0 producing dual camelCase/snake_case
instanceOf checks that create union types TS 6 can't index into.
"""

import glob
import os

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "packages", "api", "models")

count = 0
for filepath in glob.glob(os.path.join(MODELS_DIR, "*.ts")):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if content.startswith("// @ts-nocheck"):
        continue

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("// @ts-nocheck\n")
        f.write(content)
    count += 1
    print(f"  Added @ts-nocheck to {os.path.basename(filepath)}")

if count == 0:
    print("  All model files already have @ts-nocheck")
