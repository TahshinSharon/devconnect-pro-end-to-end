#!/usr/bin/env python3
"""Update all DevConnect image tags in k8s/overlays/prod/kustomization.yaml."""
from __future__ import annotations

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("Usage: update-kustomize-images.py <new-tag>")

new_tag = sys.argv[1]
path = Path("k8s/overlays/prod/kustomization.yaml")
text = path.read_text()

images = [
    "tahshin27031/devconnect-frontend",
    "tahshin27031/devconnect-auth-service",
    "tahshin27031/devconnect-user-service",
    "tahshin27031/devconnect-post-service",
    "tahshin27031/devconnect-like-service",
    "tahshin27031/devconnect-comment-service",
    "tahshin27031/devconnect-analytics-service",
    "tahshin27031/devconnect-worker-service",
]

for image in images:
    pattern = rf"(name:\s*{re.escape(image)}\n\s*newTag:\s*)([^\n]+)"
    text, count = re.subn(pattern, rf"\g<1>{new_tag}", text)
    if count != 1:
        raise SystemExit(f"Could not update image tag for {image}")

path.write_text(text)
print(f"Updated DevConnect image tags to {new_tag}")
