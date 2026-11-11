import type { Content, Outcome, SessionState } from "./types";
import type { InputMode } from "./audio/voiceDraft";

export class ApiError extends Error {
  /** `code` marks a limit of the game itself (empty text, too short), as opposed to language advice. */
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal, slowMs = 75000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const quick = method === "PUT" || path.endsWith("/location") || path.includes("/hotspots/") || (method === "GET" && !path.endsWith("/outcome"));
  const timer = window.setTimeout(() => controller.abort(), quick ? 15000 : slowMs);
  try {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: controller.signal,
  });
  if (!res.ok) {
    let detail: string | null = null;
    let code: string | undefined;
    try {
      const j = await res.json();
      if (typeof j.code === "string") code = j.code;
      if (typeof j.detail === "string") detail = j.detail;
      else if (Array.isArray(j.detail)) {
        const labels: Record<string, string> = {draft: "Email", email: "Email", text: "Reply", prediction: "Prediction", backup_plan: "Backup plan"};
        detail = j.detail.map((issue: {loc?: string[]; type?: string; ctx?: {max_length?: number}; msg?: string}) => {
          const field = issue.loc?.[issue.loc.length - 1] ?? "Input";
          if (issue.type === "string_too_long") return `${labels[field] ?? field} exceeds the ${issue.ctx?.max_length}-character limit. Shorten it and try again.`;
          return issue.msg ?? "Check your input and try again.";
        }).join(" ");
      }
    } catch {
      /* not JSON: usually the dev proxy could not reach the backend */
    }
    if (detail === null) {
      detail =
        res.status >= 500
          ? "The game is temporarily unavailable. Please retry when your connection is back."
          : res.statusText || "The request could not be completed. Please try again.";
    }
    throw new ApiError(res.status, detail, code);
  }
  return await res.json() as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError(0, "This is taking too long. Your progress may already be saved; retry to recover it.");
    throw new ApiError(0, "Connection interrupted. Your input is still here. Check your connection and retry.");
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `r-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

type TurnResponse = SessionState & { reply: string; mood: string; decision: string; available: boolean };

export interface SpeechStatus {
  available: boolean;
  backend: string;
  model: string;
  max_seconds: number;
  max_bytes: number;
  sample_rate: number;
  message?: string;
}

async function transcribeSpeech(sid: string, attempt: number, audio: Blob, signal: AbortSignal): Promise<{text: string; model: string; duration_seconds: number}> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = window.setTimeout(abort, 75000);
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sid)}/speech/transcribe?attempt=${attempt}`, {
      method: "POST", headers: { "Content-Type": "audio/wav" }, body: audio, signal: controller.signal,
    });
    if (!response.ok) {
      let message = "Couldn't turn this recording into text. Please try again or type your reply.";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch { /* Keep the readable fallback. */ }
      throw new ApiError(response.status, message);
    }
    const result = await response.json();
    if (typeof result.text !== "string") throw new ApiError(0, "No readable transcript was returned. Please try again or type your reply.");
    if (Array.from(result.text).length > 6000) throw new ApiError(0, "The transcript was unexpectedly long. Please try a shorter recording or type your reply.");
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError(0, "Speech recognition took too long. Please try again or type your reply.");
    throw new ApiError(0, "Speech recognition couldn't connect. You can keep typing and try recording again later.");
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

async function exportRecord(sid: string, includeText = false, signal?: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = window.setTimeout(abort, 15000);
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sid)}/export?include_text=${includeText ? "true" : "false"}`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      let message = "Couldn't prepare your record. Please try again.";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch { /* Use the readable fallback for non-JSON errors. */ }
      throw new ApiError(response.status, message);
    }
    const contentType = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
    if (contentType !== "application/zip") {
      throw new ApiError(response.status, "The record wasn't returned in a downloadable format. Please retry.");
    }
    const blob = await response.blob();
    if (blob.size === 0) throw new ApiError(response.status, "The downloaded record was empty. Please retry.");
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /(?:^|;)\s*filename=(?:"([^"]+)"|([^;]+))/i.exec(disposition);
    const candidate = (match?.[1] ?? match?.[2] ?? "").trim();
    // Accept only the server's expected record basename, never paths or arbitrary names.
    const filename = /^ccq-record-[A-Za-z0-9_-]{1,80}\.zip$/.test(candidate) ? candidate : "ccq-record.zip";
    return { blob, filename };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError(0, "Preparing the download took too long. Please retry.");
    throw new ApiError(0, "Couldn't download your record. Check your connection and retry.");
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export const api = {
  content: () => call<Content>("GET", "/content/chapter2"),
  createSession: () => call<SessionState>("POST", "/sessions"),
  getSession: (sid: string) => call<SessionState>("GET", `/sessions/${sid}`),
  setPhase: (sid: string, phase: "explore" | "prepare") => call<SessionState>("POST", `/sessions/${sid}/phase`, { phase }),
  setPlayer: (sid: string, name: string) => call<SessionState>("PUT", `/sessions/${sid}/player`, { name }),
  setLocation: (sid: string, area: string) => call<SessionState>("POST", `/sessions/${sid}/location`, { area }),
  viewHotspot: (sid: string, id: string, topic?: string) =>
    call<SessionState>("POST", `/sessions/${sid}/hotspots/${id}${topic ? `?topic=${encodeURIComponent(topic)}` : ""}`),
  saveDraft: (sid: string, draft: string) => call<SessionState>("PUT", `/sessions/${sid}/prep/draft`, { draft }),
  // A check is two model calls (the points, then a second reading), so it gets a longer wait.
  // The result is stored on the server for that draft version, so a timeout here loses nothing.
  feedback: (sid: string, draft: string, request_id?: string) =>
    call<SessionState & { available: boolean; version: number; reused: boolean }>(
      "POST", `/sessions/${sid}/prep/feedback`, { draft, request_id }, undefined, 110000),
  recheck: (sid: string, draft: string, request_id?: string) =>
    call<SessionState & { available: boolean; version: number; base_version: number }>(
      "POST", `/sessions/${sid}/prep/recheck`, { draft, request_id }),
  feedbackDecision: (sid: string, version: number, item_id: string, decision: "keep" | "clear") =>
    call<SessionState>("POST", `/sessions/${sid}/prep/feedback/decision`, { version, item_id, decision }),
  send: (sid: string, email: string, acknowledged_open_points = false) =>
    call<SessionState & { summary_available: boolean }>("POST", `/sessions/${sid}/prep/send`, { email, acknowledged_open_points }),
  editAgain: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/prep/edit`),
  confirmSummary: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/prep/confirm-summary`),
  prediction: (sid: string, prediction: string, backup_plan: string) =>
    call<SessionState>("POST", `/sessions/${sid}/prep/prediction`, { prediction, backup_plan }),
  savePrediction: (sid: string, prediction: string, backup_plan: string) =>
    call<SessionState>("PUT", `/sessions/${sid}/prep/prediction`, { prediction, backup_plan }),
  start: (sid: string) => call<SessionState & {available: boolean}>("POST", `/sessions/${sid}/negotiate/start`),
  turn: (sid: string, text: string, request_id: string, input_mode: InputMode = "typed", asr_text?: string) =>
    call<TurnResponse>("POST", `/sessions/${sid}/negotiate/turn`, {
      text, request_id, input_mode, ...(input_mode === "typed" ? {} : { asr_text }),
    }),
  confirm: (sid: string) => call<SessionState & { decision: string }>("POST", `/sessions/${sid}/negotiate/confirm`),
  hint: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/negotiate/hint`),
  retry: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/negotiate/retry`),
  finish: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/negotiate/finish`),
  resume: (sid: string) => call<SessionState>("POST", `/sessions/${sid}/negotiate/resume`),
  outcome: (sid: string) => call<Outcome>("GET", `/sessions/${sid}/outcome`),
  exportRecord,
  speechStatus: (signal?: AbortSignal) => call<SpeechStatus>("GET", "/speech/status", undefined, signal),
  transcribeSpeech,
};
