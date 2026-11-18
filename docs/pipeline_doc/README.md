# Fog pipeline page

`docs/fog_pipeline.html` is one self-contained page (images inlined) that documents the cloud pipeline stage by stage.

- `gen.py`: makes the figures on the PC (needs the local wind/fog libraries and a beta snapshot).
- `shot.py`: headless viewer screenshots.
- `build.py`: assembles the page from `assets/*.webp`, `facts.json` and the beta snapshot JSON.
