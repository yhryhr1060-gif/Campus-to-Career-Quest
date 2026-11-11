export type InputMode = "typed" | "voice" | "voice_edited";
export type InputMetadata = { input_mode: InputMode; asr_text?: string };

export function inputMetadata(text: string, asrText: string | null): InputMetadata {
  if (asrText === null || !text.trim()) return { input_mode: "typed" };
  return { input_mode: text.trim() === asrText.trim() ? "voice" : "voice_edited", asr_text: asrText };
}

export function sameInputMetadata(first: InputMetadata | null, second: InputMetadata): boolean {
  return first?.input_mode === second.input_mode && first.asr_text === second.asr_text;
}

export function readInputMetadata(value: unknown): InputMetadata | null {
  if (typeof value !== "object" || value === null || !("input_mode" in value)) return null;
  if (value.input_mode === "typed") return { input_mode: "typed" };
  if ((value.input_mode === "voice" || value.input_mode === "voice_edited") &&
      "asr_text" in value && typeof value.asr_text === "string") {
    return { input_mode: value.input_mode, asr_text: value.asr_text };
  }
  return null;
}
