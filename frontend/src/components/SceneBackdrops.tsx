// Public code export: original geometric SVG placeholders for the three locations.
// The owner-supplied illustrations are deliberately excluded; see src/assets/scenes/SOURCES.md.
// Place names, marker labels and dialogue carry the meaning.
//
// A backdrop is drawn on a "canvas" box that has the picture's own proportions and always covers the
// stage (cropped, never stretched). Markers are placed on the same box in fractions of the picture, so
// they stay on their objects at every window size and crop.
import type { CSSProperties } from "react";
import library from "../assets/scenes/library-desk.svg";
import corridor from "../assets/scenes/campus-corridor.svg";
import office from "../assets/scenes/professor-office.svg";

export interface Pin { x: number; y: number; lift?: boolean }
export interface Backdrop {
  src: string;
  width: number;
  height: number;
  /** Which part of the picture is kept when the window crops it (0 = left/top, 1 = right/bottom). */
  focusX: number;
  focusY: number;
  /** Where each thing is in the picture, as fractions of its width and height. */
  pins: Record<string, Pin>;
  /** The opening caption goes on the side that leaves the things in the picture clear. */
  captionSide: "left" | "right";
}

const W = 1672, H = 941;
const at = (x: number, y: number, lift = false): Pin => ({ x: x / W, y: y / H, lift });

export const BACKDROPS: Record<string, Backdrop> = {
  library: {
    src: library, width: W, height: H, focusX: 0, focusY: 0.9, captionSide: "right",
    // laptop and spiral notebook are in the picture; the phone is not, and is drawn on top (PhoneOnDesk)
    pins: { laptop: at(385, 662), syllabus: at(603, 655, true), phone: at(752, 664) },
  },
  corridor: {
    src: corridor, width: W, height: H, focusX: 0, focusY: 0.35, captionSide: "left",
    pins: { noticeboard: at(285, 300) },
  },
  office: {
    src: office, width: W, height: H, focusX: 1, focusY: 0.45, captionSide: "left",
    pins: { office_door: at(1330, 400) },
  },
};

export const canvasStyle = (b: Backdrop): CSSProperties =>
  ({ "--ar": b.width / b.height, "--fx": b.focusX, "--fy": b.focusY } as CSSProperties);

/** The picture itself. */
export function BackdropImage({ backdrop }: { backdrop: Backdrop }) {
  return <img className="canvas-img" src={backdrop.src} alt="" draggable={false} />;
}

/** The learner's phone. It is not in the library picture, so a small drawn phone is laid on the desk at
 *  its pin. This is an overlay made in code, not part of the supplied picture. */
export function PhoneOnDesk({ pin }: { pin: Pin }) {
  return (
    <svg className="canvas-prop prop-phone" style={{ left: `${pin.x * 100}%`, top: `${pin.y * 100}%` }} viewBox="0 0 60 36" aria-hidden="true" focusable="false">
      <ellipse cx="31" cy="30" rx="24" ry="4" fill="rgba(60,30,10,0.28)" />
      <g transform="rotate(-9 30 18)">
        <rect x="8" y="9" width="44" height="20" rx="4" fill="#2b2a2e" />
        <rect x="10.5" y="11" width="39" height="16" rx="2.5" fill="#3d4654" />
        <rect x="13" y="13.5" width="16" height="2.4" rx="1.2" fill="#f3e3bd" />
        <rect x="13" y="18" width="24" height="2" rx="1" fill="#b9c2cf" />
        <circle cx="46" cy="14" r="1.6" fill="#f0b955" />
      </g>
    </svg>
  );
}
