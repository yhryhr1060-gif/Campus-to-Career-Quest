// Public code export: two original geometric SVG placeholders.
// The owner-supplied illustrations are deliberately excluded; see src/assets/cafe/SOURCES.md.
// Both are decorative here: the place name, Ivy's label and the dialogue carry the meaning.
import background from "../assets/cafe/cafe-background.svg";
import ivy from "../assets/cafe/ivy-standing.svg";

/** Layer 1: the room. Fills the stage and is cropped, never stretched (object-fit: cover). */
export function CafeBackdrop({ className }: { className?: string }) {
  return <img className={className} src={background} alt="" draggable={false} />;
}

/** Layer 2: Ivy. Keeps her proportions (object-fit: contain); the image ends at the thigh. */
export function IvyFigure({ className }: { className?: string }) {
  return <img className={className} src={ivy} alt="" draggable={false} />;
}
