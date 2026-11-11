import { useEffect, useRef, useState } from "react";
import { api, type SpeechStatus } from "../api";
import { recordingSupported, recordingToWav, recordStream, stopTracks } from "../audio/recording";
import { stopSpeaking } from "../audio/tts";

type Phase = "idle" | "permission" | "recording" | "transcribing";
type Recording = {
  controller: AbortController;
  stream: MediaStream | null;
  capture: ReturnType<typeof recordStream> | null;
};

function microphoneError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      return "Microphone access was denied. Allow it in your browser to record, or keep typing.";
    }
    if (error.name === "NotFoundError") return "No microphone was found. Connect one or keep typing.";
    if (error.name === "NotReadableError") return "The microphone couldn't be opened. Check whether another app is using it, or keep typing.";
  }
  return error instanceof Error ? error.message : "Recording isn't available right now. You can keep typing.";
}

export default function VoiceInput({ sessionId, attempt, disabled, hasDraft, onBusyChange, onTranscript }: {
  sessionId: string;
  attempt: number;
  disabled: boolean;
  hasDraft: boolean;
  onBusyChange: (busy: boolean) => void;
  onTranscript: (text: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [status, setStatus] = useState<SpeechStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false);
  const operation = useRef<Recording | null>(null);
  const context = `${sessionId}:${attempt}`;
  const currentContext = useRef(context);
  currentContext.current = context;
  const callbacks = useRef({ onBusyChange, onTranscript });
  callbacks.current = { onBusyChange, onTranscript };
  const supported = recordingSupported();

  const release = (recording: Recording) => {
    recording.controller.abort();
    recording.capture?.cancel();
    if (recording.stream) stopTracks(recording.stream);
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (operation.current) release(operation.current);
      operation.current = null;
      callbacks.current.onBusyChange(false);
    };
  }, [context]);

  useEffect(() => {
    const controller = new AbortController();
    setStatus(null);
    setStatusError(null);
    if (supported) {
      api.speechStatus(controller.signal).then((value) => {
        if (!controller.signal.aborted) setStatus(value);
      }).catch((cause: unknown) => {
        if (!controller.signal.aborted) setStatusError(microphoneError(cause));
      });
    }
    return () => controller.abort();
  }, [supported, statusAttempt]);

  const cancel = () => {
    const current = operation.current;
    operation.current = null;
    if (current) release(current);
    setPhase("idle");
    setSeconds(0);
    setError(null);
    callbacks.current.onBusyChange(false);
  };

  const start = async () => {
    if (operation.current || disabled || hasDraft || !status?.available || !supported) return;
    const current: Recording = { controller: new AbortController(), stream: null, capture: null };
    operation.current = current;
    const isCurrent = () => mounted.current && currentContext.current === context && operation.current === current;
    const failed = (cause: unknown) => {
      if (!isCurrent()) return;
      operation.current = null;
      release(current);
      setPhase("idle");
      setError(microphoneError(cause));
      callbacks.current.onBusyChange(false);
    };
    stopSpeaking();
    setError(null);
    setSeconds(0);
    setPhase("permission");
    callbacks.current.onBusyChange(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false,
      });
      if (!isCurrent()) { stopTracks(stream); return; }
      current.stream = stream;
      setPhase("recording");
      current.capture = recordStream(stream, {
        maxSeconds: status.max_seconds,
        onElapsed: (elapsed) => { if (isCurrent()) setSeconds(elapsed); },
        onError: failed,
        onComplete: async (recorded) => {
          if (!isCurrent()) return;
          current.capture = null;
          current.stream = null;
          setPhase("transcribing");
          try {
            const wav = await recordingToWav(recorded, status, current.controller.signal);
            if (!isCurrent()) return;
            const result = await api.transcribeSpeech(sessionId, attempt, wav, current.controller.signal);
            if (!isCurrent()) return;
            if (!result.text.trim()) throw new Error("No speech was recognised. Please record again or type your reply.");
            operation.current = null;
            setPhase("idle");
            callbacks.current.onTranscript(result.text);
            callbacks.current.onBusyChange(false);
          } catch (cause) { failed(cause); }
        },
      });
    } catch (cause) { failed(cause); }
  };

  return (
    <section className="voice-input" aria-label="Record your reply">
      <div className="row">
        {phase === "idle" ? (
          <button disabled={disabled || hasDraft || !supported || !status?.available} onClick={() => void start()}>
            Record reply · 录音
          </button>
        ) : <>
          {phase === "recording" && <button onClick={() => operation.current?.capture?.stop()}>Stop and transcribe · 停止转文字</button>}
          <button onClick={cancel}>Cancel · 取消</button>
        </>}
        {phase === "recording" && <span className="small" role="timer">Recording {seconds.toFixed(1)} / {status?.max_seconds ?? 20}s</span>}
      </div>
      {phase === "permission" && <p className="muted small" role="status">Waiting for microphone permission. You can cancel while the browser prompt is open.</p>}
      {phase === "transcribing" && <p className="muted small" role="status">Turning your recording into text… Nothing will be sent to Prof. Chen yet.</p>}
      {phase === "idle" && <>
        {!supported ? <p className="muted small">This browser can't record here. Use a supported browser on localhost or a secure connection, or keep typing.</p>
          : statusError ? <p className="muted small">Couldn't check voice input. You can keep typing.</p>
          : !status ? <p className="muted small" role="status">Checking voice input… You can keep typing.</p>
          : !status.available ? <p className="muted small">{status.message ?? "The speech model isn't ready on this computer."} You can keep typing.</p>
          : hasDraft ? <p className="muted small">Send or clear your current reply before recording.</p>
          : <p className="muted small">Speak for up to {status.max_seconds} seconds, then check the text before pressing Send.</p>}
        {supported && (statusError || status?.available === false) && <button className="link small" onClick={() => setStatusAttempt((value) => value + 1)}>Check voice input again</button>}
      </>}
      {error && <p className="voice-error small" role="alert">{error}</p>}
    </section>
  );
}
