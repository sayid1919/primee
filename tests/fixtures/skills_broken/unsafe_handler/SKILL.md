---
name: unsafehandler
version: 1.0.0
description: "Tries to point the handler outside its own directory."
triggers:
  - anything
exclusions: []
required_permissions: []
inputs: []
outputs: []
persistence:
  vault_writes: false
handler: "../../../etc/passwd:run"
---

# Unsafe handler
