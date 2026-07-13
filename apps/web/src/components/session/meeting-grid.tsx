"use client";

import { Users } from "lucide-react";

import { ParticipantTile } from "@/components/session/participant-tile";
import { useSessionStore } from "@/store/session-store";

export function MeetingGrid() {
  const participants = useSessionStore((s) => s.participants);
  const order = useSessionStore((s) => s.participantOrder);
  const runStatus = useSessionStore((s) => s.runStatus);

  if (order.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
        <Users className="size-6" />
        {runStatus === "idle" ? "Start the run to see participants join." : "Waiting for the first participant…"}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
      {order.map((id) => (
        <ParticipantTile key={id} participant={participants[id]} />
      ))}
    </div>
  );
}
