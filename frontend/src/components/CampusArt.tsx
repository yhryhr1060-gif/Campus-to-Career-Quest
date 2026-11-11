// Hand-drawn, local SVG scene art and small icons for the compact campus. No external assets,
// fonts or tracking. Shapes only: no real people are depicted; avatars are initials on a colour.
import type { ReactNode } from "react";

export interface PlaceTheme { color: string; ink: string; tint: string; zh: string }

/** Per-place colour and a short Chinese place name (an operation aid, not a translation of story text). */
export const PLACE_THEME: Record<string, PlaceTheme> = {
  library: { color: "#5b8a72", ink: "#2f5a45", tint: "#eaf2ed", zh: "图书馆" },
  corridor: { color: "#4f6d8f", ink: "#2c4766", tint: "#e9eef5", zh: "走廊" },
  cafe: { color: "#b0703c", ink: "#7a4519", tint: "#f8eee4", zh: "咖啡角" },
  office: { color: "#80557a", ink: "#5a3354", tint: "#f3ecf2", zh: "教授办公室" },
};
const FALLBACK: PlaceTheme = { color: "#6b7178", ink: "#3c4248", tint: "#f0f1f2", zh: "" };
export const themeFor = (areaId: string): PlaceTheme => PLACE_THEME[areaId] ?? FALLBACK;

function Library() {
  return (
    <>
      <rect width="320" height="150" fill="#e6efe9" />
      <rect x="0" y="118" width="320" height="32" fill="#c9d8cd" />
      {/* bookshelf */}
      <rect x="16" y="18" width="92" height="104" rx="3" fill="#8a6a4f" />
      {[30, 62, 94].map((y) => <rect key={y} x="22" y={y} width="80" height="3" fill="#6d5139" />)}
      {[[24, "#d9785f"], [33, "#5b8a72"], [41, "#e2b74f"], [48, "#4f6d8f"], [58, "#c9cfd3"], [66, "#a35d78"], [75, "#5b8a72"], [84, "#d9785f"], [92, "#e2b74f"]]
        .map(([x, c], i) => <rect key={`a${i}`} x={x as number} y={i % 2 ? 12 + 10 : 12 + 7} width="7" height={i % 2 ? 11 : 14} fill={c as string} transform="translate(0 0)" />)}
      {[[24, "#4f6d8f"], [32, "#e2b74f"], [42, "#d9785f"], [50, "#5b8a72"], [60, "#a35d78"], [70, "#4f6d8f"], [80, "#c9cfd3"], [90, "#5b8a72"]]
        .map(([x, c], i) => <rect key={`b${i}`} x={x as number} y={i % 3 ? 44 : 41} width="8" height={i % 3 ? 18 : 21} fill={c as string} />)}
      {[[24, "#e2b74f"], [34, "#5b8a72"], [44, "#d9785f"], [56, "#4f6d8f"], [66, "#e2b74f"], [78, "#a35d78"], [88, "#5b8a72"]]
        .map(([x, c], i) => <rect key={`c${i}`} x={x as number} y={i % 2 ? 76 : 74} width="9" height={i % 2 ? 18 : 20} fill={c as string} />)}
      {/* window */}
      <rect x="132" y="18" width="70" height="56" rx="4" fill="#cfe3ef" stroke="#ffffff" strokeWidth="4" />
      <line x1="167" y1="18" x2="167" y2="74" stroke="#ffffff" strokeWidth="3" />
      <circle cx="187" cy="34" r="7" fill="#f4e3a1" />
      {/* desk, laptop, lamp, phone */}
      <rect x="126" y="96" width="176" height="8" rx="2" fill="#a07a58" />
      <rect x="136" y="104" width="6" height="30" fill="#8a6a4f" /><rect x="286" y="104" width="6" height="30" fill="#8a6a4f" />
      <rect x="178" y="70" width="46" height="26" rx="2" fill="#3c4a57" /><rect x="181" y="73" width="40" height="20" fill="#9fc2d6" />
      <rect x="170" y="94" width="62" height="3" rx="1" fill="#59656f" />
      <rect x="244" y="88" width="10" height="8" rx="2" fill="#2c3238" />
      <path d="M140 92 L160 91 L162 96 L138 96 Z" fill="#fffdf5" stroke="#b9b29a" strokeWidth="0.6" />{/* the syllabus sheet */}
      <path d="M276 96 L276 70 L292 60" stroke="#4a5560" strokeWidth="3" fill="none" />
      <path d="M284 54 L302 60 L294 70 Z" fill="#e2b74f" />
    </>
  );
}

function Corridor() {
  return (
    <>
      <rect width="320" height="150" fill="#e8edf4" />
      <path d="M0 150 L80 110 L240 110 L320 150 Z" fill="#cfd8e3" />
      <rect x="80" y="20" width="160" height="90" fill="#dde5ef" />
      {/* doors */}
      <rect x="22" y="36" width="44" height="96" rx="2" fill="#6f8aa8" /><circle cx="58" cy="86" r="2.5" fill="#e2b74f" />
      <rect x="254" y="36" width="44" height="96" rx="2" fill="#6f8aa8" /><circle cx="262" cy="86" r="2.5" fill="#e2b74f" />
      <rect x="128" y="44" width="30" height="66" fill="#8aa1bb" /><rect x="166" y="44" width="30" height="66" fill="#8aa1bb" />
      {/* notice board */}
      <rect x="96" y="28" width="128" height="62" rx="3" fill="#c99b6d" stroke="#8a6a4f" strokeWidth="3" />
      <rect x="106" y="36" width="34" height="26" fill="#ffffff" transform="rotate(-4 123 49)" />
      <rect x="146" y="38" width="30" height="40" fill="#fff6c9" />
      <rect x="182" y="36" width="32" height="24" fill="#e3eef8" transform="rotate(3 198 48)" />
      {[[123, 37], [161, 39], [198, 37]].map(([x, y]) => <circle key={x} cx={x} cy={y} r="2.5" fill="#d9785f" />)}
      {[44, 50, 56].map((y) => <rect key={y} x="150" y={y + 6} width="22" height="2" fill="#a8a07a" />)}
      <rect x="0" y="12" width="320" height="6" fill="#c3cfdc" />
    </>
  );
}

function Cafe() {
  return (
    <>
      <rect width="320" height="150" fill="#f6ece2" />
      <rect x="0" y="120" width="320" height="30" fill="#e2cdb8" />
      {/* vending machines */}
      <rect x="18" y="30" width="48" height="94" rx="4" fill="#d9785f" /><rect x="25" y="38" width="34" height="50" rx="2" fill="#fbe6d8" />
      <rect x="72" y="30" width="48" height="94" rx="4" fill="#4f6d8f" /><rect x="79" y="38" width="34" height="50" rx="2" fill="#e3eef8" />
      {[46, 58, 70].map((y) => <rect key={y} x="28" y={y} width="28" height="6" rx="1" fill="#e9a68f" />)}
      {[46, 58, 70].map((y) => <rect key={y} x="82" y={y} width="28" height="6" rx="1" fill="#9fb6cf" />)}
      {/* lamp */}
      <line x1="226" y1="0" x2="226" y2="34" stroke="#7a4519" strokeWidth="2" />
      <path d="M210 46 L242 46 L234 32 L218 32 Z" fill="#e2b74f" />
      {/* table and cups */}
      <ellipse cx="226" cy="98" rx="62" ry="10" fill="#a0683d" />
      <rect x="222" y="104" width="8" height="26" fill="#7a4519" />
      <rect x="198" y="80" width="14" height="16" rx="2" fill="#ffffff" /><path d="M212 84 q7 4 0 8" stroke="#ffffff" strokeWidth="3" fill="none" />
      <rect x="240" y="82" width="14" height="14" rx="2" fill="#f1d9c1" />
      <path d="M203 74 q3 -5 0 -10 M208 74 q3 -5 0 -10" stroke="#c9b29a" strokeWidth="2" fill="none" />
      {/* chairs */}
      <rect x="148" y="86" width="10" height="44" rx="3" fill="#b0703c" /><rect x="294" y="86" width="10" height="44" rx="3" fill="#b0703c" />
    </>
  );
}

function Office() {
  return (
    <>
      <rect width="320" height="150" fill="#f1eaf0" />
      <rect x="0" y="122" width="320" height="28" fill="#dccfd9" />
      {/* door frame, half-open door with light */}
      <rect x="118" y="22" width="84" height="104" fill="#5a3354" />
      <path d="M124 28 L196 28 L196 126 L124 126 Z" fill="#fff4cf" />
      <path d="M124 28 L160 36 L160 122 L124 126 Z" fill="#9b6d94" />
      <circle cx="154" cy="82" r="3" fill="#e2b74f" />
      <path d="M196 126 L250 150 L170 150 L124 126 Z" fill="#fff4cf" opacity="0.55" />
      {/* nameplate */}
      <rect x="214" y="44" width="58" height="22" rx="3" fill="#ffffff" stroke="#80557a" strokeWidth="2" />
      <rect x="221" y="51" width="30" height="3" fill="#80557a" /><rect x="221" y="57" width="20" height="3" fill="#b9a2b5" />
      {/* plant and chair */}
      <rect x="38" y="104" width="26" height="22" rx="3" fill="#b0703c" />
      <path d="M51 104 C40 86 34 78 40 66 M51 104 C54 84 60 76 70 70 M51 104 C50 90 52 74 52 60" stroke="#5b8a72" strokeWidth="5" fill="none" strokeLinecap="round" />
      <rect x="262" y="96" width="34" height="8" rx="2" fill="#80557a" /><rect x="266" y="104" width="4" height="22" fill="#5a3354" /><rect x="288" y="104" width="4" height="22" fill="#5a3354" />
      <rect x="292" y="70" width="6" height="34" rx="2" fill="#80557a" />
    </>
  );
}

const ART: Record<string, () => ReactNode> = { library: Library, corridor: Corridor, cafe: Cafe, office: Office };

/** Decorative scene picture. Hidden from assistive technology: the place name and description carry the meaning. */
export function SceneArt({ areaId, className }: { areaId: string; className?: string }) {
  const Art = ART[areaId];
  return (
    <svg className={className} viewBox="0 0 320 150" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      {Art ? <Art /> : <rect width="320" height="150" fill={themeFor(areaId).tint} />}
    </svg>
  );
}

/** Start-screen picture: the university building at two in the afternoon. */
export function CampusSkyline() {
  return (
    <svg className="skyline" viewBox="0 0 560 140" preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">
      <rect width="560" height="140" fill="#e7eef4" />
      <circle cx="470" cy="34" r="16" fill="#f4e3a1" />
      <rect x="0" y="116" width="560" height="24" fill="#b9d0bf" />
      <rect x="150" y="40" width="260" height="78" fill="#d9cfc2" />
      <path d="M140 42 L280 10 L420 42 Z" fill="#a35d4f" />
      <rect x="262" y="70" width="36" height="48" rx="2" fill="#6d5139" />
      {[170, 200, 230, 320, 350, 380].map((x) => <rect key={x} x={x} y="56" width="16" height="22" rx="2" fill="#cfe3ef" />)}
      {[170, 200, 230, 320, 350, 380].map((x) => <rect key={`l${x}`} x={x} y="86" width="16" height="22" rx="2" fill="#cfe3ef" />)}
      <rect x="40" y="70" width="80" height="48" fill="#c9d3dc" /><rect x="440" y="62" width="90" height="56" fill="#c9d3dc" />
      {[[60, 96], [100, 92], [480, 90], [516, 98]].map(([x, y]) => <circle key={x} cx={x} cy={y} r="18" fill="#7fa88e" />)}
    </svg>
  );
}

const ICONS: Record<string, ReactNode> = {
  look: <><circle cx="10.5" cy="10.5" r="6" /><path d="M15 15 L20 20" /></>,
  talk: <path d="M4 5 h16 v10 h-9 l-5 4 v-4 h-2 z" />,
  listen: <><path d="M4 10 v4 h3 l4 4 V6 L7 10 z" /><path d="M15 9.5 a3.5 3.5 0 0 1 0 5" /><path d="M17.8 7 a7 7 0 0 1 0 10" /></>,
  write: <><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3.5 7 L12 13.5 L20.5 7" /></>,
  pin: <><path d="M12 21 s-6 -6 -6 -11 a6 6 0 0 1 12 0 c0 5 -6 11 -6 11 z" /><circle cx="12" cy="10" r="2.2" /></>,
  phone: <><rect x="7" y="2.5" width="10" height="19" rx="2" /><path d="M11 18.5 h2" /></>,
  chevron: <path d="M9 6 L15 12 L9 18" />,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11 V8 a4 4 0 0 1 8 0 v3" /></>,
  task: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4 h6 v3 h-6 z M8.5 12 l2 2 l4 -4 M8.5 17 h7" /></>,
  notes: <><path d="M6 3 h9 l4 4 v14 h-13 z" /><path d="M9 10 h7 M9 14 h7 M9 18 h4" /></>,
  phrases: <><path d="M4 5 h16 v10 h-9 l-5 4 v-4 h-2 z" /><path d="M8 9 h8 M8 12 h5" /></>,
  close: <path d="M6 6 L18 18 M18 6 L6 18" />,
  settings: <><path d="M4 7 h9 M17 7 h3 M4 12 h3 M11 12 h9 M4 17 h11 M19 17 h1" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="12" r="2" /><circle cx="17" cy="17" r="2" /></>,
};

export function Icon({ name, className }: { name: keyof typeof ICONS | string; className?: string }) {
  return (
    <svg className={`icon ${className ?? ""}`} viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name] ?? ICONS.look}
    </svg>
  );
}

const AVATAR_COLOURS = ["#b0703c", "#5b8a72", "#4f6d8f", "#80557a", "#a35d4f"];

/** Placeholder avatar: the first letter on a colour. Not a portrait of anyone. */
export function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const letter = Array.from(name.trim())[0]?.toUpperCase() ?? "?";
  const colour = AVATAR_COLOURS[Array.from(name).reduce((sum, ch) => sum + (ch.codePointAt(0) ?? 0), 0) % AVATAR_COLOURS.length];
  return (
    <span className="avatar" aria-hidden="true" style={{ width: size, height: size, background: colour, fontSize: size * 0.45 }}>
      {letter}
    </span>
  );
}
