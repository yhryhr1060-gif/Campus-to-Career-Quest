export type SaveState = "saving" | "saved" | "error";

/** Save state of one clue. Shown next to what was read; never hidden by folding or drawers. */
export default function SaveLine({ state, retry, what }: { state: SaveState | undefined; retry: () => void; what: string }) {
  if (!state) return null;
  return (
    <p className={`save-line small save-line-${state}`} role="status" aria-live="polite">
      {state === "saving" && `Saving ${what} to your notes…`}
      {state === "saved" && `✓ Added to your notes.`}
      {state === "error" && (
        <><strong>Not saved.</strong> This is not in your notes yet (the game could not be reached).{" "}
          <button className="link" onClick={retry}>Retry saving</button></>
      )}
    </p>
  );
}
