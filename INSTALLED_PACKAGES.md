# Software installed for the v3 MVP (so it can be removed later)

Everything installed on this PC for this project is logged here: name, version, how it was installed, how to uninstall.
Python packages go in a project virtual environment (.venv in this folder) so deleting that folder removes them.

| Date | Item | Version | Installed by | Uninstall |
|---|---|---|---|---|
| (nothing installed yet) | | | | |
| Wed 11/11/2026 | "Node.js LTS" | latest at install time | winget install OpenJS.NodeJS.LTS | winget uninstall OpenJS.NodeJS.LTS |
| Wed 11/11/2026 | "Python 3.12" | already present, not installed by Claude | - | do not remove |
| Wed 11/11/2026 | "Git" | already present, not installed by Claude | - | do not remove |

| 2026-11-11 | Node.js LTS | v26.13.1 | install_tools.bat (winget) | winget uninstall OpenJS.NodeJS.LTS |
| 2026-11-11 | Python packages (pip, in .venv) | see requirements-mc.txt (56 lines, incl. xlb 0.3.2, warp-lang 1.19.0, jax 0.12.0.1) | pip into .venv | delete the .venv folder |
| 2026-11-11 | Python 3.12.10 and Git 2.56 | already on the PC | not installed by Claude | do not remove |

Notes: the first table rows above were written by install_tools.bat; the Node.js row there has no version, the row just above has it. Other files created: bench/ (benchmark, gitignored), pip.log, pip.err (safe to delete).
| 2026-11-11 | mapbox-earcut (pip, in .venv) | see requirements-mc.txt | pip into .venv | delete the .venv folder |
| 2026-11-12 | (wind direction library) no new packages | - | only existing .venv packages used | - |

| 2026-11-12 | pytest 9.2.0 (+pluggy 1.7.0, iniconfig 2.3.1, colorama 0.4.6) | 9.2.0 | pip into the main repo .venv (C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\.venv), live-data step 1 | delete the .venv folder or pip uninstall pytest pluggy iniconfig colorama |
| 2026-11-12 | (east wind directions, winddir runner) no new packages | - | only existing .venv packages used | - |
