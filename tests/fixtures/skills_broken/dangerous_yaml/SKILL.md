---
name: dangerous
version: 1.0.0
description: "Uses a YAML tag, which Primee must refuse."
triggers: !!python/object/apply:os.system ["echo pwned"]
exclusions: []
required_permissions: []
inputs: []
outputs: []
persistence:
  vault_writes: false
handler: handler.py:run
---

# Dangerous YAML
