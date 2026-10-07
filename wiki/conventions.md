---
title: Conventions
type: index
updated: 2026-10-07
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
- **Make guarantees structural.** Withhold a tool rather than instructing the
  model not to call it; never write a file rather than deleting it afterwards;
  attach output rather than asking the model to emit it. Anything phrased as
  "the model should not…" eventually will.
- **Error messages steer the model.** A misattributed error is worse than a blunt
  one — "could not read the source image" for a scoping bug sent the model
  hunting for URLs it then invented.
- **Audit the seams.** Features here work in isolation and fail where they meet.
  Exercise the real path, then sweep every layer, including logs.
