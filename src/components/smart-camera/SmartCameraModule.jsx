import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FaceDetection } from "@mediapipe/face_detection";
import { SelfieSegmentation } from "@mediapipe/selfie_segmentation";

const SETTINGS_STORAGE_KEY = "safecloak.smartCamera.settings.v1";
const DEFAULT_SETTINGS = {
  mirrorPreview: true,
  autoFrame: true,
  backgroundBlur: false,
  smartLighting: false,
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const lerp = (from, to, alpha) => from + (to - from) * alpha;

const createOffscreenCanvas = () => {
  if (typeof OffscreenCanvas !== "undefined") {
    return new OffscreenCanvas(2, 2);
  }
  const canvas = document.createElement("canvas");
  canvas.width = 2;
  canvas.height = 2;
  return canvas;
};

const readStoredSettings = () => {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw);
    return {
      mirrorPreview: parsed?.mirrorPreview ?? DEFAULT_SETTINGS.mirrorPreview,
      autoFrame: parsed?.autoFrame ?? DEFAULT_SETTINGS.autoFrame,
      backgroundBlur: parsed?.backgroundBlur ?? DEFAULT_SETTINGS.backgroundBlur,
      smartLighting: parsed?.smartLighting ?? DEFAULT_SETTINGS.smartLighting,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
};

const pickLargestFace = (detections) => {
  if (!Array.isArray(detections) || detections.length === 0) return null;

  let largest = null;
  let largestArea = 0;

  for (const detection of detections) {
    const box = detection?.locationData?.relativeBoundingBox;
    if (!box) continue;

    const x = clamp(box.xmin ?? 0, 0, 1);
    const y = clamp(box.ymin ?? 0, 0, 1);
    const width = clamp(box.width ?? 0, 0, 1);
    const height = clamp(box.height ?? 0, 0, 1);
    const area = width * height;

    if (area > largestArea) {
      largestArea = area;
      largest = {
        x,
        y,
        width,
        height,
        cx: clamp(x + width / 2, 0, 1),
        cy: clamp(y + height / 2, 0, 1),
      };
    }
  }

  return largest;
};

const computeAutoFrameTarget = (faceBox, frameWidth, frameHeight) => {
  if (!faceBox) {
    return { tx: 0, ty: 0, scale: 1 };
  }

  const faceRatioTarget = 0.38;
  const scaleFromWidth = faceRatioTarget / Math.max(faceBox.width, 0.0001);
  const scale = clamp(scaleFromWidth, 1, 1.7);

  const offsetX = clamp(0.5 - faceBox.cx, -0.5, 0.5);
  const offsetY = clamp(0.42 - faceBox.cy, -0.5, 0.5);

  const tx = clamp(offsetX * frameWidth * 0.45, -frameWidth * 0.18, frameWidth * 0.18);
  const ty = clamp(offsetY * frameHeight * 0.45, -frameHeight * 0.18, frameHeight * 0.18);

  return { tx, ty, scale };
};

const applySmartLighting = (ctx, faceBox, width, height) => {
  if (!faceBox) return;

  const padX = faceBox.width * 0.35;
  const padY = faceBox.height * 0.45;

  const sx = clamp(Math.floor((faceBox.x - padX) * width), 0, width - 1);
  const sy = clamp(Math.floor((faceBox.y - padY) * height), 0, height - 1);
  const sw = clamp(Math.ceil((faceBox.width + padX * 2) * width), 1, width - sx);
  const sh = clamp(Math.ceil((faceBox.height + padY * 2) * height), 1, height - sy);

  if (sw < 4 || sh < 4) return;

  const imageData = ctx.getImageData(sx, sy, sw, sh);
  const data = imageData.data;

  let sum = 0;
  let sumSq = 0;
  let count = 0;

  for (let i = 0; i < data.length; i += 16) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    sum += luma;
    sumSq += luma * luma;
    count += 1;
  }

  if (!count) return;

  const mean = sum / count;
  const variance = Math.max(sumSq / count - mean * mean, 0);
  const stdDev = Math.sqrt(variance);

  if (mean < 35) return;

  let gain = mean < 80 ? 1.04 : 1.08;
  let contrast = mean < 80 ? 1.01 : 1.03;

  if (stdDev > 65 && mean < 95) {
    gain = Math.min(gain, 1.025);
    contrast = 1.0;
  }

  const centerX = sw * 0.5;
  const centerY = sh * 0.42;
  const radiusX = sw * 0.5;
  const radiusY = sh * 0.58;
  const contrastPivot = 128;

  for (let y = 0; y < sh; y += 1) {
    for (let x = 0; x < sw; x += 1) {
      const dx = (x - centerX) / radiusX;
      const dy = (y - centerY) / radiusY;
      const distSq = dx * dx + dy * dy;
      if (distSq > 1) continue;

      const weight = (1 - distSq) * 0.34;
      if (weight <= 0) continue;

      const idx = (y * sw + x) * 4;
      const localGain = 1 + (gain - 1) * weight;
      const localContrast = 1 + (contrast - 1) * weight;

      const r = data[idx];
      const g = data[idx + 1];
      const b = data[idx + 2];

      const nr = clamp(((r - contrastPivot) * localContrast + contrastPivot) * localGain, 0, 255);
      const ng = clamp(((g - contrastPivot) * localContrast + contrastPivot) * localGain, 0, 255);
      const nb = clamp(((b - contrastPivot) * localContrast + contrastPivot) * localGain, 0, 255);

      data[idx] = nr;
      data[idx + 1] = ng;
      data[idx + 2] = nb;
    }
  }

  ctx.putImageData(imageData, sx, sy);
};

const CameraControls = memo(function CameraControls({ settings, onToggle }) {
  const items = useMemo(
    () => [
      { key: "mirrorPreview", label: "Mirror Preview", defaultOn: true },
      { key: "autoFrame", label: "Auto Frame", defaultOn: true },
      { key: "backgroundBlur", label: "Background Blur", defaultOn: false },
      { key: "smartLighting", label: "Smart Lighting", defaultOn: false },
    ],
    []
  );

  return (
    <div className="absolute right-3 top-3 z-20 w-64 rounded-xl border border-slate-200 bg-white/95 p-3 shadow-lg backdrop-blur">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Smart Camera
      </h3>
      <div className="space-y-2">
        {items.map((item) => {
          const enabled = Boolean(settings[item.key]);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onToggle(item.key)}
              className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-2.5 py-2 text-left text-sm transition hover:bg-slate-50"
            >
              <span className="text-slate-700">{item.label}</span>
              <span
                className={[
                  "inline-flex min-w-11 items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold",
                  enabled ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500",
                ].join(" ")}
              >
                {enabled ? "ON" : "OFF"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
});

const VideoRenderer = memo(function VideoRenderer({ stream, settings, videoRef, canvasRef }) {
  useEffect(() => {
    const videoEl = videoRef.current;
    if (!videoEl) return;
    if (videoEl.srcObject !== stream) {
      videoEl.srcObject = stream ?? null;
    }
  }, [stream, videoRef]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-2xl bg-slate-950">
      <video ref={videoRef} autoPlay playsInline muted className="hidden" />
      <canvas
        ref={canvasRef}
        className={[
          "h-full w-full object-cover transition-transform duration-150 ease-out",
          settings.mirrorPreview ? "scale-x-[-1]" : "",
        ].join(" ")}
      />
    </div>
  );
});

function FaceTracker({ videoRef, enabled, faceBoxRef }) {
  useEffect(() => {
    let rafId = 0;
    let lastRun = 0;
    let disposed = false;
    let running = false;
    let detector = null;

    const init = async () => {
      detector = new FaceDetection({
        locateFile: (file) =>
          `https://cdn.jsdelivr.net/npm/@mediapipe/face_detection/${file}`,
      });

      detector.setOptions({
        model: "short",
        minDetectionConfidence: 0.6,
      });

      detector.onResults((results) => {
        faceBoxRef.current = pickLargestFace(results?.detections ?? []);
      });

      const run = async (time) => {
        if (disposed) return;

        const videoEl = videoRef.current;
        const shouldRun =
          enabled &&
          videoEl &&
          videoEl.readyState >= 2 &&
          videoEl.videoWidth > 0 &&
          !running &&
          time - lastRun >= 100;

        if (shouldRun) {
          running = true;
          lastRun = time;
          try {
            await detector.send({ image: videoEl });
          } catch {
            // ignore runtime frame errors
          } finally {
            running = false;
          }
        }

        if (!enabled) {
          faceBoxRef.current = null;
        }

        rafId = requestAnimationFrame(run);
      };

      rafId = requestAnimationFrame(run);
    };

    init();

    return () => {
      disposed = true;
      if (rafId) cancelAnimationFrame(rafId);
      faceBoxRef.current = null;
      if (detector && typeof detector.close === "function") {
        detector.close();
      }
    };
  }, [enabled, faceBoxRef, videoRef]);

  return null;
}

function EffectsProcessor({ videoRef, canvasRef, settings, faceBoxRef }) {
  const settingsRef = useRef(settings);
  const segmentationMaskRef = useRef(null);
  const transformRef = useRef({ tx: 0, ty: 0, scale: 1 });
  const compositionRef = useRef({
    base: createOffscreenCanvas(),
    blur: createOffscreenCanvas(),
    out: createOffscreenCanvas(),
  });

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  useEffect(() => {
    let rafId = 0;
    let disposed = false;
    let segmenter = null;
    let segBusy = false;
    let lastSegRun = 0;

    const ensureCanvasSize = (canvas, width, height) => {
      if (!canvas) return;
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
    };

    const renderFrame = (time) => {
      if (disposed) return;

      const videoEl = videoRef.current;
      const canvasEl = canvasRef.current;

      if (!videoEl || !canvasEl || videoEl.readyState < 2 || videoEl.videoWidth === 0) {
        rafId = requestAnimationFrame(renderFrame);
        return;
      }

      const width = videoEl.videoWidth;
      const height = videoEl.videoHeight;

      ensureCanvasSize(canvasEl, width, height);

      const baseCanvas = compositionRef.current.base;
      const blurCanvas = compositionRef.current.blur;
      const outCanvas = compositionRef.current.out;

      ensureCanvasSize(baseCanvas, width, height);
      ensureCanvasSize(blurCanvas, width, height);
      ensureCanvasSize(outCanvas, width, height);

      const baseCtx = baseCanvas.getContext("2d", { willReadFrequently: false });
      const blurCtx = blurCanvas.getContext("2d", { willReadFrequently: false });
      const outCtx = outCanvas.getContext("2d", { willReadFrequently: true });

      if (!baseCtx || !blurCtx || !outCtx) {
        rafId = requestAnimationFrame(renderFrame);
        return;
      }

      const runtime = settingsRef.current;
      const faceBox = faceBoxRef.current;

      if (
        runtime.backgroundBlur &&
        segmenter &&
        !segBusy &&
        time - lastSegRun >= 140 &&
        videoEl.readyState >= 2
      ) {
        segBusy = true;
        lastSegRun = time;
        segmenter
          .send({ image: videoEl })
          .catch(() => {})
          .finally(() => {
            segBusy = false;
          });
      }

      baseCtx.clearRect(0, 0, width, height);
      baseCtx.drawImage(videoEl, 0, 0, width, height);

      outCtx.clearRect(0, 0, width, height);
      outCtx.drawImage(baseCanvas, 0, 0, width, height);

      if (runtime.backgroundBlur && segmentationMaskRef.current) {
        blurCtx.clearRect(0, 0, width, height);
        blurCtx.filter = "blur(14px)";
        blurCtx.drawImage(videoEl, 0, 0, width, height);
        blurCtx.filter = "none";

        outCtx.clearRect(0, 0, width, height);
        outCtx.drawImage(blurCanvas, 0, 0, width, height);
        outCtx.globalCompositeOperation = "destination-out";
        outCtx.drawImage(segmentationMaskRef.current, 0, 0, width, height);
        outCtx.globalCompositeOperation = "destination-over";
        outCtx.drawImage(baseCanvas, 0, 0, width, height);
        outCtx.globalCompositeOperation = "source-over";
      }

      if (runtime.smartLighting && faceBox) {
        applySmartLighting(outCtx, faceBox, width, height);
      }

      const target = runtime.autoFrame
        ? computeAutoFrameTarget(faceBox, width, height)
        : { tx: 0, ty: 0, scale: 1 };

      const current = transformRef.current;
      current.tx = lerp(current.tx, target.tx, 0.12);
      current.ty = lerp(current.ty, target.ty, 0.12);
      current.scale = lerp(current.scale, target.scale, 0.12);

      const displayCtx = canvasEl.getContext("2d");
      if (displayCtx) {
        displayCtx.clearRect(0, 0, width, height);
        displayCtx.save();
        displayCtx.translate(width * 0.5, height * 0.5);
        displayCtx.translate(current.tx, current.ty);
        displayCtx.scale(current.scale, current.scale);
        displayCtx.translate(-width * 0.5, -height * 0.5);
        displayCtx.drawImage(outCanvas, 0, 0, width, height);
        displayCtx.restore();
      }

      rafId = requestAnimationFrame(renderFrame);
    };

    const init = async () => {
      segmenter = new SelfieSegmentation({
        locateFile: (file) =>
          `https://cdn.jsdelivr.net/npm/@mediapipe/selfie_segmentation/${file}`,
      });
      segmenter.setOptions({ modelSelection: 1 });
      segmenter.onResults((results) => {
        segmentationMaskRef.current = results?.segmentationMask ?? null;
      });

      rafId = requestAnimationFrame(renderFrame);
    };

    init();

    return () => {
      disposed = true;
      if (rafId) cancelAnimationFrame(rafId);
      segmentationMaskRef.current = null;
      if (segmenter && typeof segmenter.close === "function") {
        segmenter.close();
      }
    };
  }, [canvasRef, faceBoxRef, videoRef]);

  return null;
}

function SmartCameraModule({ stream }) {
  const [settings, setSettings] = useState(readStoredSettings);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const faceBoxRef = useRef(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const toggleSetting = useCallback((key) => {
    setSettings((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  return (
    <div className="relative h-full w-full">
      <VideoRenderer stream={stream} settings={settings} videoRef={videoRef} canvasRef={canvasRef} />
      <FaceTracker videoRef={videoRef} enabled={settings.autoFrame || settings.smartLighting} faceBoxRef={faceBoxRef} />
      <EffectsProcessor videoRef={videoRef} canvasRef={canvasRef} settings={settings} faceBoxRef={faceBoxRef} />
      <CameraControls settings={settings} onToggle={toggleSetting} />
    </div>
  );
}

export default memo(SmartCameraModule);
