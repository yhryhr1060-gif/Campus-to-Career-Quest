import { encodePcm16Wav, mixChannels, type AudioLimits } from "./wav";

export function recordingSupported(): boolean {
  return typeof window !== "undefined" && !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined" && typeof AudioContext !== "undefined" &&
    typeof OfflineAudioContext !== "undefined";
}

export function stopTracks(stream: MediaStream) {
  stream.getTracks().forEach((track) => track.stop());
}

export function recordStream(stream: MediaStream, options: {
  maxSeconds: number;
  onElapsed: (seconds: number) => void;
  onComplete: (blob: Blob) => void;
  onError: (error: Error) => void;
}): { stop: () => void; cancel: () => void } {
  const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]
    .find((type) => MediaRecorder.isTypeSupported(type));
  const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  const chunks: Blob[] = [];
  let cancelled = false;
  let stopping = false;
  let ended = false;
  let tick: ReturnType<typeof setInterval> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const tracks = stream.getTracks();
  const cleanup = () => {
    clearInterval(tick);
    clearTimeout(deadline);
    tracks.forEach((track) => track.removeEventListener("ended", disconnected));
    stopTracks(stream);
  };
  const fail = (message: string) => {
    if (ended || cancelled) return;
    ended = true;
    cleanup();
    try { if (recorder.state !== "inactive") recorder.stop(); } catch { /* already stopped */ }
    options.onError(new Error(message));
  };
  const disconnected = () => {
    if (!stopping) fail("The microphone disconnected. You can reconnect it and record again, or keep typing.");
  };
  const stop = () => {
    if (ended || stopping || cancelled) return;
    stopping = true;
    clearInterval(tick);
    clearTimeout(deadline);
    try {
      if (recorder.state !== "inactive") recorder.stop();
      else fail("Recording stopped unexpectedly. Please record again or type your reply.");
    } catch { fail("Couldn't finish the recording. Please record again or type your reply."); }
    // MediaRecorder queues its final data event; the microphone can be released now.
    stopTracks(stream);
  };
  recorder.ondataavailable = (event) => { if (!cancelled && !ended && event.data.size) chunks.push(event.data); };
  recorder.onerror = () => fail("Couldn't record from this microphone. You can keep typing.");
  recorder.onstop = () => {
    cleanup();
    if (cancelled || ended) return;
    ended = true;
    const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
    if (!blob.size) options.onError(new Error("No audio was recorded. Please try again or type your reply."));
    else options.onComplete(blob);
  };
  tracks.forEach((track) => track.addEventListener("ended", disconnected));
  try {
    recorder.start(250);
    const startedAt = performance.now();
    tick = setInterval(() => options.onElapsed(Math.min(options.maxSeconds, (performance.now() - startedAt) / 1000)), 100);
    // Leave room for encoded tail frames; keep the entire recording rather than cutting it.
    deadline = setTimeout(stop, Math.max(0, options.maxSeconds * 1000 - 500));
  } catch (error) {
    cleanup();
    throw error;
  }
  return { stop, cancel: () => {
    cancelled = true;
    cleanup();
    try { if (recorder.state !== "inactive") recorder.stop(); } catch { /* already stopped */ }
  } };
}

export async function recordingToWav(blob: Blob, limits: AudioLimits, signal: AbortSignal): Promise<Blob> {
  const check = () => { if (signal.aborted) throw new DOMException("Recording cancelled", "AbortError"); };
  check();
  const decoder = new AudioContext();
  const close = () => { if (decoder.state !== "closed") void decoder.close().catch(() => undefined); };
  signal.addEventListener("abort", close, { once: true });
  let source: AudioBufferSourceNode | null = null;
  try {
    const encoded = await blob.arrayBuffer();
    check();
    const decoded = await decoder.decodeAudioData(encoded);
    check();
    const frames = Math.round(decoded.length * limits.sample_rate / decoded.sampleRate);
    if (frames / limits.sample_rate > limits.max_seconds || 44 + frames * 2 > limits.max_bytes) {
      throw new Error(`The recording ran beyond ${limits.max_seconds} seconds. Please record a shorter reply; no audio was sent.`);
    }
    if (frames <= 0) throw new Error("No audio was recorded. Please try again or type your reply.");
    const channels = Array.from({ length: decoded.numberOfChannels }, (_, index) => decoded.getChannelData(index));
    const mono = decoder.createBuffer(1, decoded.length, decoded.sampleRate);
    mono.copyToChannel(mixChannels(channels), 0);
    const offline = new OfflineAudioContext(1, frames, limits.sample_rate);
    source = offline.createBufferSource();
    source.buffer = mono;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    check();
    return new Blob([encodePcm16Wav(rendered.getChannelData(0), limits)], { type: "audio/wav" });
  } finally {
    source?.disconnect();
    signal.removeEventListener("abort", close);
    close();
  }
}
