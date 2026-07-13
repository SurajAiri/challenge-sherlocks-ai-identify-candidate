"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { EnginePanel } from "@/components/session/engine-panel";
import { MeetingGrid } from "@/components/session/meeting-grid";
import { RawEventLog } from "@/components/session/raw-event-log";
import { ScenarioInfoPanel } from "@/components/session/scenario-info-panel";
import { SessionControls } from "@/components/session/session-controls";
import { Topbar } from "@/components/session/topbar";
import { TranscriptPanel } from "@/components/session/transcript-panel";
import { buttonVariants } from "@/components/ui/button";
import { EngineSocket, getEngineWsUrl } from "@/lib/engine-client";
import { startSimulatorRun } from "@/lib/simulator-client";
import { cn } from "@/lib/utils";
import { useScenarioLibraryStore } from "@/store/scenario-library-store";
import { useSessionStore } from "@/store/session-store";

export function SessionClient({ scenarioId }: { scenarioId: string }) {
  const scenario = useScenarioLibraryStore((s) => s.getById(scenarioId));
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const runStatus = useSessionStore((s) => s.runStatus);
  const runAbort = useSessionStore((s) => s.runAbort);
  const runSpeedMultiplier = useSessionStore((s) => s.runSpeedMultiplier);
  const engineStatus = useSessionStore((s) => s.engineStatus);
  const startSession = useSessionStore((s) => s.startSession);
  const setRunStatus = useSessionStore((s) => s.setRunStatus);
  const setRunAbort = useSessionStore((s) => s.setRunAbort);
  const setEngineStatus = useSessionStore((s) => s.setEngineStatus);
  const handleSimFrame = useSessionStore((s) => s.handleSimFrame);
  const handleEngineMessage = useSessionStore((s) => s.handleEngineMessage);

  const engineSocketRef = useRef<EngineSocket | null>(null);
  const initializedFor = useRef<string | null>(null);

  // Reset session state exactly once per scenario, and keep one Engine
  // socket alive for the lifetime of this page.
  useEffect(() => {
    if (!scenario) return;
    if (initializedFor.current === scenario.id) return;
    initializedFor.current = scenario.id;
    startSession({ libraryId: scenario.id, path: scenario.path, name: scenario.name });

    const socket = new EngineSocket(getEngineWsUrl());
    engineSocketRef.current = socket;
    const offStatus = socket.onStatus(setEngineStatus);
    const offMessage = socket.onMessage(handleEngineMessage);
    socket.connect();

    return () => {
      offStatus();
      offMessage();
      socket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario?.id]);

  if (!mounted) return null;

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
    setRunStatus("connecting");
    const controller = startSimulatorRun(
      scenario!.path,
      {
        onOpen: () => setRunStatus("streaming"),
        onFrame: (frame) => {
          handleSimFrame(frame);
          // Forward every frame to the Engine, same shape it arrived in.
          // Silently a no-op until the Engine exists / is connected.
          engineSocketRef.current?.send(frame);
        },
        onDone: () => setRunStatus("completed"),
        onError: (message) => setRunStatus("error", message),
      },
      runSpeedMultiplier
    );
    setRunAbort(controller);
  }

  function handleStop() {
    runAbort?.abort();
    setRunAbort(null);
    setRunStatus("idle");
  }

  return (
    <div className="flex flex-1 flex-col">
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
          <EnginePanel />
        </div>
      </div>
    </div>
  );
}
