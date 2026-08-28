---
title: Conventions
type: index
updated: 2026-08-28
---

# Conventions

- **Configuration over code.** Anything deployment-specific belongs in
  `config.py` and the environment. No hostnames or model filenames in logic.
- **Optional by default.** A new tool must degrade gracefully: unconfigured means
  invisible, never broken.
- **Tool descriptions are part of the product.** They steer model behaviour more
  than the implementation does. Be directive.
- **Measure before optimising.** Every performance claim in this wiki carries a
  measurement or a cited source.
- **Document the traps.** A failure that cost hours belongs in the wiki, with the
  symptom that identified it — silent failures especially.
