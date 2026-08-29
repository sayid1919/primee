---
name: alpha
version: 9.9.9
description: "Duplicate of alpha, must be rejected by the loader."
triggers:
  - duplicate
exclusions: []
required_permissions: []
inputs: []
outputs:
  - name: message
    type: string
persistence:
  vault_writes: false
handler: handler.py:run
---

# Duplicate alpha
