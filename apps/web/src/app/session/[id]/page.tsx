import { SessionClient } from "@/components/session/session-client";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionClient scenarioId={id} />;
}
