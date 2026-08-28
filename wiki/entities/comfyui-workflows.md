---
title: ComfyUI workflows
type: entity
tags: [images, configuration]
updated: 2026-08-28
sources: [workflows/, gateway/workflow.py]
verified_at: 2026-08-28
---

# ComfyUI workflows

Image workflows are **user-supplied JSON in `workflows/`**, not code. Node graphs
and model filenames differ per install, so hardcoding them would make the project
unusable for anyone else.

Export from ComfyUI: **Workflow → Export (API)** — note the *API* format, which is
a flat `node_id → {class_type, inputs}` map, not the editor format.

Placeholders substituted at call time: `{{prompt}}`, `{{width}}`, `{{height}}`,
`{{seed}}`, `{{steps}}`, `{{instruction}}`, `{{image}}`.

## Trap: hand-built graphs fail silently

A hand-assembled edit workflow returned a **perfect copy of the input image with
the instruction ignored** — no error, no warning. The cause was omitted nodes that
looked optional but were not (a flow-matching sampling shift and a guidance
normaliser).

**Always diff a hand-built workflow against the bundled template for that model**
before concluding the model is at fault. ComfyUI ships hundreds of templates under
`Workflow → Browse Templates`.
