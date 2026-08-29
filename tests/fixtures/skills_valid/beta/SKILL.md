---
name: beta
version: 2.1.0
description: "Test skill that also mentions coffee."
triggers:
  - coffee
  - "coffee grinder"
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

# Beta

Test fixture skill used for ambiguity tests.
