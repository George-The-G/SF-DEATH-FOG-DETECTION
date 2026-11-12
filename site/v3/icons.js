// Custom line-art play-button icons for SF Death Fog v3 (no emoji needed).
// All: viewBox 0 0 24 24, stroke-based, currentColor, round caps/joins, aria-hidden.
// Wire-up: btn.innerHTML = ICON_SKATE_PLAY;  CSS: .btn svg{width:26px;height:26px}
// (stroke/fill/width attributes are baked in, so CSS `color` alone recolours them.)

const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const svg = (body) => open + body + '</svg>';

// Shared parts
const TRI_OUT = '<path d="M8 3.5 17.5 9 8 14.5Z"/>';
const TRI_SOLID = '<path d="M8 3.5 17.5 9 8 14.5Z" fill="currentColor"/>';
const BOARD = '<path d="M3 16.5q.4 1.5 2 1.5h14q1.6 0 2-1.5"/><circle cx="8" cy="20.6" r="1.3"/><circle cx="16" cy="20.6" r="1.3"/>';
const WAVES = '<path d="M3 17.2q2.25-2.2 4.5 0t4.5 0 4.5 0 4.5 0"/><path d="M3 21.2q2.25-2.2 4.5 0t4.5 0 4.5 0 4.5 0"/>';

// Option A: outline triangle with line art beneath
export const ICON_SKATE_PLAY_A = svg(TRI_OUT + BOARD);
export const ICON_FOG_PLAY_A = svg(TRI_OUT + WAVES);

// Option B: solid triangle + line art
export const ICON_SKATE_PLAY_B = svg(TRI_SOLID + BOARD);
export const ICON_FOG_PLAY_B = svg(TRI_SOLID + WAVES);

// Option C: large line art, small triangle
export const ICON_SKATE_PLAY_C = svg(
  '<path d="M9.5 4.5 15 8 9.5 11.5Z"/>' +
  '<path d="M2.5 13.5q.5 2 2.5 2h14q2 0 2.5-2"/><circle cx="7.5" cy="19.3" r="1.6"/><circle cx="16.5" cy="19.3" r="1.6"/>');
export const ICON_FOG_PLAY_C = svg(
  '<path d="M7 19.5a4.2 4.2 0 0 1-.6-8.35A5.8 5.8 0 0 1 17.4 9.8 4.9 4.9 0 0 1 17.5 19.5Z"/>' +
  '<path d="M10.5 11.2 14.5 14l-4 2.8Z"/>');

// Recommended defaults: skateboard A, cloud C
export const ICON_SKATE_PLAY = ICON_SKATE_PLAY_A;
export const ICON_FOG_PLAY = ICON_FOG_PLAY_C; // cloud+play reads as fog better than waves (waves look like water); use _A for style consistency

export const ICONS = { ICON_SKATE_PLAY, ICON_FOG_PLAY };
