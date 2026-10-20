"use client";

import { useEffect, useRef, useState } from "react";
import { uploadMedia } from "@/lib/api";
import type { MediaIntake } from "@/lib/types";
import { Icon } from "./Icons";

export default function MediaCapture({
  disabled,
  onMedia,
  onTranscript,
  media,
  onRemove,
  onBusy,
  showUpload = false,
}: {
  disabled: boolean;
  onMedia: (media: MediaIntake, replaceId?: string) => void;
  onTranscript: (text: string) => void;
  media: MediaIntake[];
  onRemove: (id: string) => void;
  onBusy: (busy: boolean) => void;
  showUpload?: boolean;
}) {
  const photoInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const alive = useRef(true);
  const previousUpload = useRef<{ file: File; id: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<{
    file: File;
    kind: "photo" | "audio";
  } | null>(null);
  const [transcript, setTranscript] = useState("");
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    onBusy(uploading || recording || confirming);
  }, [uploading, recording, confirming, onBusy]);

  const stopTracks = () => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    if (timer.current) clearTimeout(timer.current);
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (recorder.current?.state === "recording") recorder.current.stop();
      stopTracks();
    };
  }, []);
  useEffect(() => {
    if (!recording) return;
    const tick = setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => clearInterval(tick);
  }, [recording]);

  async function upload(file: File, kind: "photo" | "audio") {
    if (uploading) return;
    setError("");
    setUploading(true);
    setRetry({ file, kind });
    try {
      const response = await uploadMedia(file, kind);
      onMedia(
        response,
        previousUpload.current?.file === file
          ? previousUpload.current.id
          : undefined,
      );
      previousUpload.current = { file, id: response.id };
      if (
        response.status === "needs_configuration" ||
        response.status === "retryable"
      )
        throw new Error(
          response.error?.message ||
            response.message ||
            "这份素材暂时没读成功，可以重试或继续输入文字。",
        );
      setRetry(null);
      if (response.transcript) {
        setTranscript(response.transcript);
        setConfirming(true);
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "素材暂时没读成功，请重试。",
      );
    } finally {
      setUploading(false);
    }
  }

  async function toggleRecord() {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    setError("");
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setError(
        "当前页面无法直接录音。可以选择一段录音文件，或在本机与 HTTPS 页面使用麦克风。",
      );
      return;
    }
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      if (!alive.current) {
        stopTracks();
        return;
      }
      const mimeType = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/webm",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const nextRecorder = new MediaRecorder(
        stream.current,
        mimeType ? { mimeType } : undefined,
      );
      chunks.current = [];
      recorder.current = nextRecorder;
      nextRecorder.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      nextRecorder.onstop = () => {
        setRecording(false);
        stopTracks();
        const blob = new Blob(chunks.current, { type: nextRecorder.mimeType });
        if (blob.size && alive.current)
          void upload(
            new File(
              [blob],
              `门店语音.${nextRecorder.mimeType.includes("mp4") ? "m4a" : "webm"}`,
              { type: blob.type },
            ),
            "audio",
          );
      };
      nextRecorder.start();
      setSeconds(0);
      setRecording(true);
      timer.current = setTimeout(() => {
        if (nextRecorder.state === "recording") nextRecorder.stop();
      }, 120_000);
    } catch {
      stopTracks();
      setError("麦克风还没有获得许可。请允许麦克风，或选择录音文件。");
    }
  }

  return (
    <div className="mq-media">
      {media.length > 0 && (
        <div className="mq-attachments">
          {media.map((item) => (
            <figure
              key={item.id}
              className={item.kind === "audio" ? "mq-audio-attachment" : ""}
            >
              {item.kind === "photo" && item.preview_url ? (
                <img
                  src={item.preview_url}
                  alt={item.observations?.caption || "已添加的门店照片"}
                />
              ) : (
                <Icon name="mic" />
              )}
              <figcaption>
                {item.kind === "audio"
                  ? "语音素材"
                  : item.observations?.caption || "门店照片"}
              </figcaption>
              {item.kind === "audio" && item.preview_url && (
                <audio
                  src={item.preview_url}
                  controls
                  preload="metadata"
                  aria-label="回听录音"
                />
              )}
              <button
                type="button"
                aria-label={`移除${item.kind === "photo" ? "照片" : "语音"}`}
                onClick={() => onRemove(item.id)}
              >
                <Icon name="close" size={16} />
              </button>
            </figure>
          ))}
        </div>
      )}
      <div className="mq-capture-actions">
        {showUpload && (
          <button
            type="button"
            disabled={disabled || uploading || recording}
            onClick={() => audioInput.current?.click()}
          >
            <Icon name="mic" />
            <span>上传录音</span>
          </button>
        )}
        <button
          type="button"
          disabled={disabled || uploading || recording}
          onClick={() => photoInput.current?.click()}
        >
          <Icon name="photo" />
          <span>拍一张</span>
        </button>
        <button
          type="button"
          className={recording ? "mq-recording" : ""}
          disabled={disabled || uploading}
          onClick={() => void toggleRecord()}
        >
          <Icon name="mic" />
          <span>
            {recording
              ? `结束录音 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
              : "说一句"}
          </span>
        </button>
        {uploading && <span role="status">正在读取素材…</span>}
        <input
          ref={photoInput}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file, "photo");
            event.target.value = "";
          }}
        />
        <input
          ref={audioInput}
          type="file"
          accept="audio/*"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file, "audio");
            event.target.value = "";
          }}
        />
      </div>
      {error && (
        <div className="mq-media-error" role="alert">
          <p>{error}</p>
          <div>
            {retry && (
              <button
                type="button"
                disabled={uploading}
                onClick={() => void upload(retry.file, retry.kind)}
              >
                重新读取
              </button>
            )}
            <button type="button" onClick={() => audioInput.current?.click()}>
              选择录音文件
            </button>
          </div>
        </div>
      )}
      {confirming && (
        <div className="mq-transcript">
          <label htmlFor="voice-transcript">刚才这段话，听对了吗？</label>
          <textarea
            id="voice-transcript"
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
          />
          <div>
            <button
              type="button"
              onClick={() => {
                onTranscript(transcript.trim());
                setConfirming(false);
              }}
              disabled={!transcript.trim()}
            >
              加入这次需求
            </button>
            <button type="button" onClick={() => setConfirming(false)}>
              暂不使用
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
