"use client";

import { useEffect, useRef } from "react";

/** Draws the latest base64 JPEG frame onto a canvas, cover-fit. Each
 * stream chunk from the simulator is a full independent JPEG (see
 * `media_gen.py`'s `extract_video_frames`), so there's no decoding
 * state to carry between frames - just draw-and-replace. */
export function WebcamCanvas({
  frameDataUrl,
  className,
}: {
  frameDataUrl: string | null;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!frameDataUrl) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const img = new Image();
    imgRef.current = img;
    img.onload = () => {
      if (imgRef.current !== img) return; // a newer frame arrived first
      const { width: cw, height: ch } = canvas;
      const scale = Math.max(cw / img.width, ch / img.height);
      const dw = img.width * scale;
      const dh = img.height * scale;
      ctx.clearRect(0, 0, cw, ch);
      ctx.drawImage(img, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
    };
    img.src = frameDataUrl;
  }, [frameDataUrl]);

  return <canvas ref={canvasRef} width={320} height={180} className={className} />;
}
