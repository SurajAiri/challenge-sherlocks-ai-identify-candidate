"use client";

import { useEffect, useRef } from "react";

import { useSessionStore } from "@/store/session-store";

/**
 * Plays session-store's liveAudioQueue back-to-back, one utterance at a
 * time, in speaking order. Purely a consumer of the queue - session-store
 * is the only thing that pushes onto it (from audio_stream_off, gated on
 * livePlaybackEnabled) and this only ever pops from the front.
 *
 * Note this is a *queue*, not a real-time channel: each entry is the
 * full, real-duration WAV for one utterance, decoded only once its
 * audio_stream_off has fully arrived. At sim speeds where events arrive
 * faster than that audio takes to play (see the >8x cutoff in
 * session-controls.tsx), this queue falls further behind every turn
 * instead of catching up - it is not designed to survive that, which is
 * exactly why the switch is force-disabled above that threshold rather
 * than left to quietly drift.
 */
export function LiveAudioPlayer() {
  const queue = useSessionStore((s) => s.liveAudioQueue);
  const dequeueLiveAudio = useSessionStore((s) => s.dequeueLiveAudio);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playingUrlRef = useRef<string | null>(null);

  useEffect(() => {
    const next = queue[0] ?? null;
    const audio = audioRef.current;
    if (!audio || !next || playingUrlRef.current === next) return;
    playingUrlRef.current = next;
    audio.src = next;
    // Autoplay can be blocked by the browser until the page has seen a
    // user gesture - starting a run always goes through a click on
    // "Start experiment" first, so this should succeed in practice, but
    // a rejected promise must not be left to wedge the queue forever.
    audio.play().catch(() => {
      playingUrlRef.current = null;
      dequeueLiveAudio();
    });
  }, [queue, dequeueLiveAudio]);

  function advance() {
    playingUrlRef.current = null;
    dequeueLiveAudio();
  }

  return <audio ref={audioRef} className="hidden" onEnded={advance} onError={advance} />;
}
