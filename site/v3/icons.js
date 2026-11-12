// Custom line-art play-button icons for SF Death Fog v3 (no emoji needed).
// Rounded play triangle (outline) with line art INSIDE it.
// All: viewBox 0 0 24 24, stroke-based, currentColor, round caps/joins, aria-hidden.
// Wire-up: btn.innerHTML = ICON_SKATE_PLAY;  CSS: .btn svg{width:26px;height:26px}
// (stroke/fill attributes are baked in, so CSS `color` alone recolours them.)

const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const svg = (body) => open + body + '</svg>';

const TRI_D = 'M3 6.4Q3 3 6.06 4.49L18.44 10.51Q21.5 12 18.44 13.49L6.06 19.51Q3 21 3 17.6Z';
const TRI_1 = `<path d="${TRI_D}"/>`;
const TRI_2 = `<path d="${TRI_D}" fill="currentColor" fill-opacity=".15"/>`;

// Skateboard, side view: kicked deck + two wheels (thinner stroke)
const BOARD = '<g stroke-width="1.2" transform="translate(9.6 12.2) scale(1.22) translate(-9.6 -12.2)"><path d="M5.3 9.9q.2 1.6 1.6 1.6h4.8q1.4 0 1.6-1.6"/><circle cx="7.3" cy="14" r=".95"/><circle cx="11.9" cy="14" r=".95"/></g>';
// Cloud outline
const CLOUD = '<g stroke-width="1.2" transform="translate(9.8 12.4) scale(1.2) translate(-9.8 -12.4)"><path d="M7.3 15a2.1 2.1 0 0 1-.1-4.2 3 3 0 0 1 5.6-.4A2.3 2.3 0 0 1 12.6 15Z"/></g>';

export const ICON_SKATE_PLAY_1 = svg(TRI_1 + BOARD);
export const ICON_SKATE_PLAY_2 = svg(TRI_2 + BOARD);
export const ICON_FOG_PLAY_1 = svg(TRI_1 + CLOUD);
export const ICON_FOG_PLAY_2 = svg(TRI_2 + CLOUD);

// Recommended
export const ICON_SKATE_PLAY = ICON_SKATE_PLAY_2;
export const ICON_FOG_PLAY = ICON_FOG_PLAY_2;

export const ICONS = { ICON_SKATE_PLAY, ICON_FOG_PLAY };
