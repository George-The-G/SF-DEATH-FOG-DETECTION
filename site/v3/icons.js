// Pill play-button icons for SF Death Fog v3.
// viewBox 0 0 46 24: [themed line-art glyph, ~24 wide] [6-7 px gap] [rounded play triangle or pause bars, 14 wide, 18 tall].
// stroke-based, currentColor, stroke 1.75, round caps/joins, aria-hidden. CSS `color` recolours / hovers / disables.
// Use: btn.querySelector('.ic').innerHTML = ICON_SKATE_PLAY;  CSS: .pb svg{height:24px;width:auto}

const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 46 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const svg = (body) => open + body + '</svg>';

const TRI = '<path d="M32 5.2Q32 3 34 4.2L44 10.8Q46 12 44 13.2L34 19.8Q32 21 32 18.8Z" fill="currentColor" fill-opacity=".15"/>';
const BARS = '<path d="M33.5 4.5V19.5M42.5 4.5V19.5" stroke-width="2.4"/>';

// Skateboard, side view: kicked deck, two trucks, two wheels (shifted down to centre on the 24 px row)
const BOARD = '<g transform="translate(-1 1.2)"><path d="M2 7.5Q3 12 7 12H21Q25 12 26 7.5"/><circle cx="8" cy="17.5" r="2.5"/><circle cx="20" cy="17.5" r="2.5"/></g>';
// Cloud outline
const CLOUD = '<path d="M5.5 20.5a4.6 4.6 0 0 1-.55-9.2 6.8 6.8 0 0 1 12.9-1.3A5.3 5.3 0 0 1 19 20.5Z" transform="translate(.8 .2)"/>';

export const ICON_SKATE_PLAY = svg(BOARD + TRI);
export const ICON_FOG_PLAY = svg(CLOUD + TRI);
export const ICON_FOG_PAUSE = svg(CLOUD + BARS);
export const ICONS = { ICON_SKATE_PLAY, ICON_FOG_PLAY, ICON_FOG_PAUSE };
