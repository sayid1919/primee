---
name: badperm
version: 1.0.0
description: "Requests a permission that does not exist."
triggers:
  - anything
exclusions: []
required_permissions:
  - vault.delete_everything
inputs: []
outputs: []
persistence:
  vault_writes: false
handler: handler.py:run
---

# Bad permission
