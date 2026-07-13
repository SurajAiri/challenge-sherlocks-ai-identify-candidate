"use client";

import { useEffect, useRef, type MutableRefObject } from "react";

import { useSessionStore, type LiveAudioChunk } from "@/store/session-store";

/**
 * Drains session-store's liveAudioChunkQueue and schedules each raw PCM
 * chunk back-to-back on a single Web Audio graph, in arrival order.
 *
 * Two things this is deliberately built to avoid, both of which the
 * previous "buffer the whole utterance into one WAV, then play it"
 * design got wrong:
 *
 * 1. Waiting for a whole utterance before making any sound. Chunks are
 *    queued the instant they arrive (see applyStreamFrame in
 *    session-store.ts), not batched until audio_stream_off - so
 *    playback starts within about one audio_chunk_ms of the mic
 *    opening, not one whole utterance later.
 *
 * 2. Falling behind at sim speeds above 1x. The PCM itself is never
 *    time-compressed - only how fast chunks are *delivered* is scaled
 *    by speed_multiplier - so at, say, 2x, twice as much real audio
 *    arrives per wall-clock second as can physically be played back at
 *    its native rate. Left alone this queue would only ever grow.
 *    Instead every scheduled chunk's `playbackRate` is set to the run's
 *    own speed_multiplier, so audio is sped up (and pitched up - this
 *    is naive resampling, not a time-stretch algorithm) by the same
 *    factor the sim clock already is. That keeps it roughly in sync
 *    indefinitely instead of drifting - at the cost of higher-pitched
 *    audio the faster you go, which is why the "Play audio" switch in
 *    session-controls.tsx is capped at 8x: past that the pitch shift
 *    stops being "sped up speech" and starts being noise.
 */
export function LiveAudioPlayer() {
  const queue = useSessionStore((s) => s.liveAudioChunkQueue);
  const dequeueLiveAudioChunks = useSessionStore((s) => s.dequeueLiveAudioChunks);
  // Known non-null whenever livePlaybackEnabled can be true - the UI
  // (session-controls.tsx) forces livePlaybackEnabled false whenever
  // this is null, so queue items only ever appear alongside a real
  // number here. Falling back to 1 is just for type-safety, never
  // meaningfully exercised.
  const runSpeedMultiplier = useSessionStore((s) => s.runSpeedMultiplier);

  const ctxRef = useRef<AudioContext | null>(null);
  const nextStartAtRef = useRef(0);

  useEffect(() => {
    if (queue.length === 0) return;
    const rate = runSpeedMultiplier ?? 1;

    const ctx = ensureContext(ctxRef, queue[0].sampleRate);
    if (ctx.state === "suspended") void ctx.resume();

    for (const chunk of queue) {
      const buffer = pcm16ToAudioBuffer(ctx, chunk);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = rate;
      source.connect(ctx.destination);
      const startAt = Math.max(nextStartAtRef.current, ctx.currentTime);
      source.start(startAt);
      nextStartAtRef.current = startAt + buffer.duration / rate;
    }

    // Every queued chunk has now been scheduled - trim exactly that many
    // off the front. Using queue.length captured at the top of this
    // effect (rather than "clear the whole array") means a chunk that
    // sneaks in between the read and this call - not possible today
    // since stream frames only ever arrive from a single SSE callback,
    // but cheap insurance against a future concurrent producer - isn't
    // silently dropped.
    dequeueLiveAudioChunks(queue.length);
  }, [queue, runSpeedMultiplier, dequeueLiveAudioChunks]);

  // Nothing to render - this is a scheduler, not an <audio> element.
  // Web Audio's AudioContext.destination is the output; no DOM node
  // is involved in actually producing sound.
  return null;
}

function ensureContext(ref: MutableRefObject<AudioContext | null>, sampleRate: number): AudioContext {
  if (!ref.current) {
    ref.current = new AudioContext({ sampleRate });
  }
  return ref.current;
}

/** Raw pcm_s16le mono/stereo bytes -> a Web Audio AudioBuffer. */
function pcm16ToAudioBuffer(ctx: AudioContext, chunk: LiveAudioChunk): AudioBuffer {
  const { bytes, sampleRate, channels } = chunk;
  const bytesPerSample = 2; // s16le
  const frameCount = Math.floor(bytes.length / bytesPerSample / channels);
  const buffer = ctx.createBuffer(channels, Math.max(frameCount, 1), sampleRate);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  for (let ch = 0; ch < channels; ch++) {
    const channelData = buffer.getChannelData(ch);
    for (let i = 0; i < frameCount; i++) {
      const byteIndex = (i * channels + ch) * bytesPerSample;
      const int16 = view.getInt16(byteIndex, /* littleEndian */ true);
      channelData[i] = int16 / 32768;
    }
  }
  return buffer;
}
