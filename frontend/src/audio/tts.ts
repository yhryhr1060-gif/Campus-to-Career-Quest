// Prof. Chen's voice: the browser's built-in speechSynthesis with one English voice.

let chosen: SpeechSynthesisVoice | null = null;

function pickVoice(): SpeechSynthesisVoice | null {
  if (!("speechSynthesis" in window)) return null;
  if (chosen) return chosen;
  const voices = window.speechSynthesis.getVoices();
  chosen =
    voices.find((v) => v.lang === "en-GB") ??
    voices.find((v) => v.lang === "en-US") ??
    voices.find((v) => v.lang.startsWith("en")) ??
    null;
  return chosen;
}

if (typeof window !== "undefined" && "speechSynthesis" in window) {
  window.speechSynthesis.onvoiceschanged = () => {
    chosen = null;
    pickVoice();
  };
}

export function ttsAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function speak(text: string, rate = 0.95): void {
  if (!ttsAvailable()) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const v = pickVoice();
  if (v) u.voice = v;
  u.lang = v?.lang ?? "en-GB";
  u.rate = rate;
  window.speechSynthesis.speak(u);
}

export function stopSpeaking(): void {
  if (ttsAvailable()) window.speechSynthesis.cancel();
}
