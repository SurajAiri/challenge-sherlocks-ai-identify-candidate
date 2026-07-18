"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";

import { EnginePanel } from "@/components/session/engine-panel";
import { LiveAudioPlayer } from "@/components/session/live-audio-player";
import { MeetingGrid } from "@/components/session/meeting-grid";
import { RawEventLog } from "@/components/session/raw-event-log";
import { ScenarioInfoPanel } from "@/components/session/scenario-info-panel";
import { SessionControls } from "@/components/session/session-controls";
import { Topbar } from "@/components/session/topbar";
import { TranscriptPanel } from "@/components/session/transcript-panel";
import { buttonVariants } from "@/components/ui/button";
import { EngineSocket, getEngineWsUrl } from "@/lib/engine-client";
import { startSimulatorRun } from "@/lib/simulator-client";
import { useMounted } from "@/lib/use-mounted";
import { cn } from "@/lib/utils";
import { useScenarioLibraryStore, useScenarioLibraryHydrated } from "@/store/scenario-library-store";
import { useSessionStore } from "@/store/session-store";

export function SessionClient({ scenarioId }: { scenarioId: string }) {
  const scenario = useScenarioLibraryStore((s) => s.getById(scenarioId));
  const mounted = useMounted();
  // The real "is the library ready to be queried" signal - see the
  // hook's own doc comment for why useMounted() alone isn't enough on
  // a fresh /session/[id] load (as opposed to an in-app Link nav).
  const libraryHydrated = useScenarioLibraryHydrated();

  const runStatus = useSessionStore((s) => s.runStatus);
  const runAbort = useSessionStore((s) => s.runAbort);
  const runSpeedMultiplier = useSessionStore((s) => s.runSpeedMultiplier);
  const engineStatus = useSessionStore((s) => s.engineStatus);
  const startSession = useSessionStore((s) => s.startSession);
  const setRunStatus = useSessionStore((s) => s.setRunStatus);
  const setRunAbort = useSessionStore((s) => s.setRunAbort);
  const setEngineStatus = useSessionStore((s) => s.setEngineStatus);
  const setEngineReconnectedMidRun = useSessionStore((s) => s.setEngineReconnectedMidRun);
  const handleSimFrame = useSessionStore((s) => s.handleSimFrame);
  const handleEngineMessage = useSessionStore((s) => s.handleEngineMessage);

  const engineSocketRef = useRef<EngineSocket | null>(null);
  const initializedFor = useRef<string | null>(null);

  // Create the Engine socket once per scenario, but do NOT connect yet.
  // Connection happens in handleStart so the engine only sees frames from
  // an active run, not stale reconnects from page reloads.
  useEffect(() => {
    // Wait for the real rehydration signal, not just scenario being
    // falsy - on a fresh page load `scenario` is legitimately
    // undefined for a beat before localStorage has been read, and
    // treating that the same as "genuinely not in the library" is
    // exactly what used to skip this effect (no EngineSocket ever
    // gets constructed) until a reload happened to land after
    // rehydration resolved.
    if (!libraryHydrated) return;
    if (!scenario) return;
    if (initializedFor.current === scenario.id) return;
    initializedFor.current = scenario.id;
    startSession({ libraryId: scenario.id, path: scenario.path, name: scenario.name });

    const socket = new EngineSocket(getEngineWsUrl());
    engineSocketRef.current = socket;
    const offStatus = socket.onStatus(setEngineStatus);
    const offMessage = socket.onMessage(handleEngineMessage);
    const offReconnect = socket.onReconnectDetected(setEngineReconnectedMidRun);
    // Do NOT call socket.connect() here — connect on Start, close on Stop/Done.

    return () => {
      // React StrictMode (on by default for Next.js dev) runs every
      // effect as mount -> cleanup -> mount again. Without resetting
      // this ref here, the *second* mount's `initializedFor.current
      // === scenario.id` guard above would see the id already set
      // from the first mount and bail out - leaving
      // engineSocketRef.current pointed at this now-closed socket,
      // with its listeners already torn down, for the rest of the
      // page's life. handleStart's later connect() would then
      // silently reopen this same dead socket: the transport-level
      // connection genuinely succeeds (which is why the Engine's own
      // logs show an accepted connection and real processing), but
      // with no onMessage/onStatus listeners left subscribed, nothing
      // ever reaches session-store - so the panel never updates until
      // a full page reload rebuilds everything from scratch. Resetting
      // the ref on every cleanup means a StrictMode-driven second
      // mount (or any other real remount) always gets a fresh,
      // properly-wired socket instead of inheriting a dead one.
      initializedFor.current = null;
      offStatus();
      offMessage();
      offReconnect();
      socket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario?.id, libraryHydrated]);

  if (!mounted || !libraryHydrated) return null;

  if (!scenario) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
        <p className="text-sm text-muted-foreground">
          This scenario isn&apos;t in your local library (maybe it was removed, or you&apos;re on a
          different browser).
        </p>
        <Link href="/" className={cn(buttonVariants({ size: "sm" }))}>
          Back to library
        </Link>
      </div>
    );
  }

  function handleStart() {
    // Re-running the same scenario ("Try again" after "completed") needs
    // to clear out the previous run's transcript/participants/log/audio
    // first - startSession is otherwise only invoked once per scenario
    // id (see the mount effect above), so without this a retry would
    // just keep appending onto stale state from the last run instead of
    // starting clean.
    if (runStatus === "completed" || runStatus === "error") {
      startSession({ libraryId: scenario!.id, path: scenario!.path, name: scenario!.name });
    }
    // Connect the engine socket now (deferred from mount so page reloads
    // don't cause a spurious connection before the user hits Start).
    engineSocketRef.current?.connect();
    setRunStatus("connecting");
    const controller = startSimulatorRun(
      scenario!.path,
      {
        onOpen: () => setRunStatus("streaming"),
        onFrame: (frame) => {
          handleSimFrame(frame);
          // Forward every frame to the Engine, same shape it arrived in.
          engineSocketRef.current?.send(frame);
        },
        onDone: () => {
          setRunStatus("completed");
          // Close the engine connection cleanly when the run finishes.
          engineSocketRef.current?.close();
        },
        onError: (message) => {
          setRunStatus("error", message);
          engineSocketRef.current?.close();
        },
      },
      runSpeedMultiplier
    );
    setRunAbort(controller);
  }

  function handleStop() {
    runAbort?.abort();
    setRunAbort(null);
    setRunStatus("idle");
    // Close the engine connection when the user manually stops the run.
    engineSocketRef.current?.close();
  }

  function handleReconnect() {
    engineSocketRef.current?.connect();
  }

  return (
    <div className="flex flex-1 flex-col">
      <LiveAudioPlayer />
      <Topbar scenarioName={scenario.name} runStatus={runStatus} engineStatus={engineStatus} />
      <div className="mx-auto grid w-full max-w-7xl flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-4">
          <SessionControls scenarioId={scenario.id} onStart={handleStart} onStop={handleStop} />
          <MeetingGrid />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <TranscriptPanel />
            <div className="flex flex-col gap-4">
              <ScenarioInfoPanel scenario={scenario} />
            </div>
          </div>
          <RawEventLog />
        </div>
        <div className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
          <EnginePanel
            onReconnect={handleReconnect}
            groundTruthParticipantId={scenario.groundTruthParticipantId}
          />
        </div>
      </div>
    </div>
  );
}
