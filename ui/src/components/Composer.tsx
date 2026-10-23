import { useEffect, useRef, useState } from "react";
import { transcribe } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "./primitives";

type RecState = "idle" | "recording" | "transcribing";

export function Composer({
  onRun,
  disabled,
  starter,
}: {
  onRun: (goal: string) => void;
  disabled?: boolean;
  starter?: string;
}) {
  const [value, setValue] = useState("");
  const [rec, setRec] = useState<RecState>("idle");
  const [err, setErr] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (starter) setValue(starter);
  }, [starter]);

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps

  function stop() {
    recorderRef.current?.stop();
    recorderRef.current = null;
  }

  async function toggleVoice() {
    if (rec === "recording") {
      stop();
      return;
    }
    if (rec === "transcribing") return;
    setErr("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setErr("No microphone available in this browser.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      r.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: r.mimeType || "audio/webm" });
        if (!blob.size) {
          setRec("idle");
          return;
        }
        setRec("transcribing");
        try {
          const text = await transcribe(blob);
          setValue((v) => (v ? v + " " + text : text).trim());
          setRec("idle");
        } catch (e) {
          setErr(e instanceof Error ? e.message : "Transcription failed.");
          setRec("idle");
        }
      };
      recorderRef.current = r;
      r.start();
      setRec("recording");
    } catch {
      setErr("Microphone permission was denied.");
    }
  }

  const canRun = value.trim().length > 0 && !disabled;

  return (
    <div className="w-full max-w-2xl">
      <div className="rounded-2xl border border-line bg-white p-3 shadow-composer">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canRun) onRun(value.trim());
            }
          }}
          rows={3}
          maxLength={4000}
          placeholder="What should the computer do?"
          aria-label="Describe a goal"
          className="w-full resize-none border-0 bg-transparent p-2 text-[15px] leading-relaxed text-ink outline-none placeholder:text-muted/70"
        />
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-1.5">
            <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-teal/30 bg-teal-soft px-2.5 text-xs font-medium text-teal">
              Computer
            </span>
            <button
              type="button"
              onClick={toggleVoice}
              aria-label={rec === "recording" ? "Stop recording" : "Dictate"}
              title="Dictate (Groq LPU)"
              className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors",
                rec === "recording" ? "bg-red-500 text-white" : "text-muted hover:bg-black/5",
              )}
            >
              {rec === "transcribing" ? (
                <span className="h-3 w-3 animate-pulse rounded-full border border-muted" />
              ) : (
                <MicIcon />
              )}
            </button>
          </div>
          <Button variant="primary" disabled={!canRun} onClick={() => onRun(value.trim())}>
            {rec === "recording" ? "Listening…" : "Run"}
          </Button>
        </div>
      </div>
      {rec !== "idle" && (
        <p className="mt-2 text-center text-xs text-muted">
          {rec === "recording" ? "Listening — click the mic to finish." : "Transcribing…"}
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