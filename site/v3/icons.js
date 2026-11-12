// Wide play-button icons for SF Death Fog v3 (pill buttons, no emoji needed).
// [themed line-art glyph] [rounded play triangle], same visual height, one row.
// viewBox 0 0 52 24, stroke-based, currentColor, stroke 1.75, round caps/joins, aria-hidden.
// Wire-up: btn.innerHTML = ICON_SKATE_PLAY;  CSS: .play-btn svg{height:24px;width:auto}
// Stroke/fill are baked in, so CSS `color` alone recolours/hover/disables them.

const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const svg = (body) => open + body + '</svg>';

const TRI_D = "M31.00 5.50Q31 2.5 33.68 3.84L47.32 10.66Q50 12 47.32 13.34L33.68 20.16Q31 21.5 31.00 18.50Z";
const TRI_1 = `<path d="${TRI_D}"/>`;                                        // outline
const TRI_2 = `<path d="${TRI_D}" fill="currentColor" fill-opacity=".15"/>`; // faint fill

// Skateboard, side view: kicked deck, two trucks, two wheels
const BOARD = '<path d="M2 7.5Q3 12 7 12H21Q25 12 26 7.5"/><circle cx="8" cy="17.5" r="2.5"/><circle cx="20" cy="17.5" r="2.5"/>';
// Cloud outline
const CLOUD = '<path d="M7 20.5a5 5 0 0 1-.6-9.96 7.4 7.4 0 0 1 14-1.4A5.7 5.7 0 0 1 21.5 20.5Z"/>';

export const ICON_SKATE_PLAY_1 = svg(BOARD + TRI_1);
export const ICON_SKATE_PLAY_2 = svg(BOARD + TRI_2);
export const ICON_FOG_PLAY_1 = svg(CLOUD + TRI_1);
export const ICON_FOG_PLAY_2 = svg(CLOUD + TRI_2);

// Recommended: faint-filled triangle (reads as "play" at a glance)
export const ICON_SKATE_PLAY = ICON_SKATE_PLAY_2;
export const ICON_FOG_PLAY = ICON_FOG_PLAY_2;

export const ICONS = { ICON_SKATE_PLAY, ICON_FOG_PLAY };
