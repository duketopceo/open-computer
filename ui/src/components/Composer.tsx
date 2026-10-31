import { useEffect, useRef, useState } from "react";
import { errorMessage, getEffort, transcribe, type GoalOptions } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useFetch } from "@/lib/useFetch";
import { EffortPicker, toOptions, type EffortChoice } from "./EffortPicker";
import { Button } from "./primitives";

type RecState = "idle" | "starting" | "recording" | "transcribing";

// Whisper's 25 MB upload cap is ~20+ min of opus; stop well before it.
const MAX_RECORDING_MS = 120_000;

function micError(e: unknown): string {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone permission was denied.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return `Couldn't start recording: ${errorMessage(e, "unknown error")}`;
}

export function Composer({
  onRun,
  disabled,
  busy,
  starter,
}: {
  onRun: (goal: string, opts: GoalOptions) => void;
  disabled?: boolean;
  busy?: boolean;
  starter?: string;
}) {
  const [value, setValue] = useState("");
  const effort = useFetch(getEffort, []);
  const catalog = effort.data;
  const [choice, setChoice] = useState<EffortChoice | null>(null);
  // Until the user picks, the server's default level is selected.
  const picked: EffortChoice = choice ?? { effort: catalog?.default ?? "", model: "", budget: "" };
  const opts = toOptions(picked, catalog);
  const [rec, setRec] = useState<RecState>("idle");
  const [err, setErr] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | undefined>();
  // Set on unmount: late getUserMedia results and pending onstop handlers
  // must release the mic and never upload audio for a view that's gone.
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (starter) setValue(starter);
  }, [starter]);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
      window.clearTimeout(timerRef.current);
      const r = recorderRef.current;
      recorderRef.current = null;
      if (r && r.state !== "inactive") r.stop(); // onstop sees cancelled → no upload
      releaseMic();
    };
  }, []);

  function releaseMic() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function stop() {
    window.clearTimeout(timerRef.current);
    const r = recorderRef.current;
    recorderRef.current = null;
    if (r && r.state !== "inactive") r.stop();
  }

  async function toggleVoice() {
    if (rec === "recording") {
      stop();
      return;
    }
    if (rec !== "idle") return;
    setErr("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setErr("Voice input isn't supported in this browser.");
      return;
    }
    setRec("starting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      if (!cancelledRef.current) {
        setErr(micError(e));
        setRec("idle");
      }
      return;
    }
    if (cancelledRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;
    try {
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      // The browser fires dataavailable + stop after error; a failed take must
      // never be uploaded.
      let failed = false;
      r.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      r.onerror = () => {
        failed = true;
        window.clearTimeout(timerRef.current);
        if (recorderRef.current === r) recorderRef.current = null;
        chunksRef.current = [];
        releaseMic();
        if (!cancelledRef.current) {
          setErr("Recording failed.");
          setRec("idle");
        }
      };
      r.onstop = async () => {
        releaseMic();
        if (cancelledRef.current || failed) return;
        const blob = new Blob(chunksRef.current, { type: r.mimeType || "audio/webm" });
        chunksRef.current = [];
        if (!blob.size) {
          setRec("idle");
          return;
        }
        setRec("transcribing");
        try {
          const text = await transcribe(blob);
          if (!cancelledRef.current) setValue((v) => (v ? v + " " + text : text).trim());
        } catch (e) {
          if (!cancelledRef.current) setErr(errorMessage(e, "Transcription failed."));
        } finally {
          if (!cancelledRef.current) setRec("idle");
        }
      };
      recorderRef.current = r;
      r.start();
      timerRef.current = window.setTimeout(stop, MAX_RECORDING_MS);
      setRec("recording");
    } catch (e) {
      recorderRef.current = null;
      releaseMic();
      setErr(micError(e));
      setRec("idle");
    }
  }

  const canRun = value.trim().length > 0 && !disabled && !busy && rec === "idle" && !effort.loading && opts !== null;
  const run = () => opts && onRun(value.trim(), opts);

  const mic = (
    <button
      type="button"
      onClick={toggleVoice}
      disabled={disabled || busy || rec === "starting" || rec === "transcribing"}
      aria-label={rec === "recording" ? "Stop recording" : "Dictate"}
      title="Dictate (Groq LPU)"
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors",
        rec === "recording" ? "bg-red-500 text-white" : "text-muted hover:bg-black/5",
      )}
    >
      {rec === "transcribing" || rec === "starting" ? (
        <span className="h-3 w-3 animate-pulse rounded-full border border-muted" />
      ) : (
        <MicIcon />
      )}
    </button>
  );

  return (
    <div className="w-full max-w-2xl">
      <div className="rounded-2xl border border-line bg-white p-3 shadow-composer">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // IME confirmation (CJK etc.) also fires Enter — never submit on it.
            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canRun) run();
            }
          }}
          rows={3}
          maxLength={4000}
          placeholder="What should the computer do?"
          aria-label="Describe a goal"
          className="w-full resize-none border-0 bg-transparent p-2 text-[15px] leading-relaxed text-ink outline-none placeholder:text-muted"
        />
        <div className="flex flex-wrap items-start justify-between gap-2 px-1">
          <div className="min-w-0 flex-1 basis-full sm:basis-0">
            {catalog ? (
              <EffortPicker catalog={catalog} value={picked} onChange={setChoice} disabled={disabled || busy} lead={mic} />
            ) : (
              <div className="flex items-center gap-2">
                {mic}
                {effort.error && (
                  <p className="text-xs text-muted" role="status">
                    Effort levels unavailable; the server default applies.
                  </p>
                )}
              </div>
            )}
          </div>
          <Button variant="primary" className="ml-auto" disabled={!canRun} aria-busy={busy} onClick={run}>
            {busy ? "Sending…" : rec === "recording" ? "Listening…" : "Run"}
          </Button>
        </div>
      </div>
      {rec !== "idle" && (
        <p className="mt-2 text-center text-xs text-muted" role="status">
          {rec === "starting"
            ? "Waiting for the microphone…"
            : rec === "recording"
              ? "Listening — click the mic to finish (2 min max)."
              : "Transcribing…"}
        </p>
      )}
      {err && (
        <p className="mt-2 text-center text-xs text-red-600" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M19 11a7 7 0 0 1-14 0M12 18v3"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}