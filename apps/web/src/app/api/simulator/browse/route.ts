import { execSync } from "child_process";
import path from "path";
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Resolves a folder name to its absolute path on disk.
 *
 * GET /api/simulator/browse?name=<folderName>
 *
 * Searches the monorepo root for any directory named <folderName>.
 * Works for both individual scenario folders (have index.yml) and parent
 * folders that contain multiple scenarios. The caller uses the expand
 * endpoint to determine which scenario paths to actually evaluate.
 */
export async function GET(req: NextRequest) {
  const name = req.nextUrl.searchParams.get("name")?.trim();

  if (!name) {
    return Response.json(
      { error: "missing_param", detail: "Query param `name` is required." },
      { status: 400 }
    );
  }

  // Reject names that could escape the search or inject shell commands
  if (/[/\\;|&`$(){}[\]<>*?!'"\n]/.test(name)) {
    return Response.json(
      { error: "invalid_name", detail: "Folder name contains invalid characters." },
      { status: 400 }
    );
  }

  // process.cwd() is apps/web when Next.js is running.
  // The monorepo root is two directories up — that's where /scenarios lives.
  const monorepoRoot = path.resolve(process.cwd(), "../..");

  try {
    // Collect ALL matching directories, then pick the shallowest one
    // (fewest path segments = closest to the project root).
    // This prevents a deeper directory with the same name (e.g.
    // playground/scenario_simulator/scenarios) from winning over the
    // intended root-level directory (e.g. ./scenarios).
    const raw = execSync(
      `find . -maxdepth 6 -type d -name "${name}" ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/.cache/*" ! -path "*/.turbo/*" 2>/dev/null`,
      { cwd: monorepoRoot, encoding: "utf8", timeout: 8000 }
    ).trim();

    if (!raw) {
      return Response.json(
        {
          error: "not_found",
          detail: `No folder named "${name}" was found under the project root. Make sure the folder is inside the project, or paste the full absolute path manually.`,
        },
        { status: 404 }
      );
    }

    // Sort by depth (number of "/" chars) and take the shallowest match
    const allMatches = raw.split("\n").filter(Boolean);
    const shallowest = allMatches.sort(
      (a, b) => (a.match(/\//g)?.length ?? 0) - (b.match(/\//g)?.length ?? 0)
    )[0];

    const absolutePath = path.resolve(monorepoRoot, shallowest);
    return Response.json({ path: absolutePath });
  } catch (err) {
    return Response.json(
      {
        error: "resolve_failed",
        detail:
          err instanceof Error ? err.message : "Failed to resolve folder path.",
      },
      { status: 500 }
    );
  }
}
