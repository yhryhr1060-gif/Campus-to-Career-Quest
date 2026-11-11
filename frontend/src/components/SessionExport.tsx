import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export default function SessionExport({ sessionId }: { sessionId: string }) {
  const [includeText, setIncludeText] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadStarted, setDownloadStarted] = useState(false);
  const mounted = useRef(false);
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    setIncludeText(false);
    setBusy(false);
    setError(null);
    setDownloadStarted(false);
    return () => {
      mounted.current = false;
      request.current?.abort();
      request.current = null;
    };
  }, [sessionId]);

  const download = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const isCurrent = () => mounted.current && currentSession.current === sessionId && request.current === controller;
    setBusy(true);
    setError(null);
    setDownloadStarted(false);
    try {
      const { blob, filename } = await api.exportRecord(sessionId, includeText, controller.signal);
      if (!isCurrent()) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.hidden = true;
      try {
        document.body.appendChild(link);
        link.click();
        // Give the browser time to take ownership of the download, then release it.
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (cause) {
        URL.revokeObjectURL(url);
        throw cause;
      } finally {
        link.remove();
      }
      setDownloadStarted(true);
    } catch (cause) {
      if (isCurrent()) setError(cause instanceof Error ? cause.message : "Couldn't download your record. Please retry.");
    } finally {
      if (isCurrent()) {
        request.current = null;
        setBusy(false);
      }
    }
  };

  return (
    <section className="panel session-export">
      <h3>Your play-through record · 本次记录</h3>
      <p className="muted small">
        By default, your record includes progress, turn counts, and service interruptions.
        Select the box to add your writing and conversation. Review that text before sharing.
      </p>
      <label className="export-option">
        <input type="checkbox" checked={includeText} disabled={busy} onChange={(event) => {
          setIncludeText(event.target.checked);
          setDownloadStarted(false);
          setError(null);
        }} />
        <span>Include my writing and conversation · 包含我的写作与对话</span>
      </label>
      <div className="row">
        <button disabled={busy} onClick={() => void download()}>
          {busy ? "Preparing download…" : error ? "Retry download · 重试下载" : "Download record · 下载记录"}
        </button>
        {busy && <span className="muted small" role="status">Preparing your record…</span>}
      </div>
      {error && <p className="export-error small" role="alert">{error}</p>}
      {downloadStarted && <p className="muted small" role="status">Download started. Check your browser's downloads. · 已开始下载。</p>}
    </section>
  );
}
