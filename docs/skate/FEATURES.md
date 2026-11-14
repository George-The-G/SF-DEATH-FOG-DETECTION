# Skate mode: feature tracking and suggestions

Own list for the Take control side feature. Keep it out of the main docs. Update the status when something ships.
Status: `shipped`, `next`, `idea` (not decided), `declined`.

## Shipped
| Feature | Notes |
|---|---|
| Camera never goes under the ground (orbit, flights, flyover) | everyone, `?noclamp=1` to compare |
| Take control button during the flyover | toy-size skater, hands over from the flyover spot and heading |
| On-screen joystick, WASD or arrows, gamepad | skater-relative steering; stick down brakes |
| Gravity downhill, drag, steering that slows with speed | `TUNE` in skate.js |
| Wet streets are slippery (slower steering, more side slip) | uses the same wetness as the street colours |
| Building walls (boxes and outlines), water and steep ground block | offline-tested on the real data |
| Soft stay-on-street help, "↺ Street" respawn, auto respawn when stuck 3 s | |
| Chase camera that stays above hills | |
| Slim bottom bar while skating | only the time bar stays |
| Slow-device guard (lower pixel ratio after about 4 s of slow frames) | |

## Next (agreed direction, not built)
| Feature | Notes |
|---|---|
| Objectives | George: "objectives later". Candidates: reach the bus stop at the end of the route, timer, no-bump run |
| Show Sunshine and Road temp value under the skater | display only, never changes handling |
| Free skate outside the flyover (start anywhere on a street) | needs a way to pick a start; the whole city has street data in live mode |

## Ideas and suggestions (not decided)
| Idea | Why | Cost guess |
|---|---|---|
| Ollie and jump | fun, tricks | medium: needs a vertical state and curb or ledge data we do not have |
| Street-level fog look check | skating into the Death Fog is the payoff; fog was designed for a high camera | small to medium: visual pass with the volumetric fog |
| Live buses as ghosts or as moving obstacles | ties into the bus work | small once the bus layer is merged |
| Sound (wheels, wind) and haptic bumps | feel | haptics already buzz on hard wall hits; sound is new |
| Finer ground (20 m height file, about 1.4 MB raw) | smoother hills close up | large: new data file and build step |
| Hide far buildings in skate mode | speed on weak phones | small |
| Ghost trail or best-run replay | objectives later | medium |
| Lite mode (stay on route rails, steer between lanes) | very weak phones | medium |

## Known limits
* The ground is a 60 m grid, so hills look faceted close up. Streets use their own height.
* No sidewalks, curbs, stairs, fences, trees, tunnels or overpasses in the data.
* All buildings are infinitely tall walls.
* Frame rate was not measured on a real phone or GPU (tested with software rendering only). Please report slow devices.
* Needs the street table: in the old replay mode (`?live=0`) only the district streets exist.

## How to add a suggestion
Add a row under "Ideas and suggestions" with what, why and a rough cost. Do not edit the main README, PROGRESS.md or plan files for skate ideas.
