---
name: alpha
version: 1.0.0
description: "Test skill that reports coffee facts."
triggers:
  - coffee
  - "coffee report"
exclusions:
  - tea
required_permissions: []
inputs: []
outputs:
  - name: message
    type: string
persistence:
  vault_writes: false
handler: handler.py:run
---

# Alpha

Test fixture skill.
