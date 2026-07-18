import fs from "fs";
import path from "path";
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Expands a directory path into a list of scenario paths to evaluate.
 *
 * GET /api/simulator/expand?dir=<absolutePath>
 *
 * Logic:
 *   1. If <dir>/index.yml exists → it is itself a scenario → return [dir]
 *   2. Otherwise → scan direct children, use statSync (not Dirent.isDirectory)
 *      to check each is a real directory with an index.yml inside it
 *   3. If neither yields any paths → return 404 with diagnostic detail
 */
export async function GET(req: NextRequest) {
  const dir = req.nextUrl.searchParams.get("dir")?.trim();

  if (!dir) {
    return Response.json(
      { error: "missing_param", detail: "Query param `dir` is required." },
      { status: 400 }
    );
  }

  if (!path.isAbsolute(dir)) {
    return Response.json(
      { error: "invalid_path", detail: "Path must be absolute." },
      { status: 400 }
    );
  }

  try {
    // Case 1: the path itself is a scenario folder
    if (fs.existsSync(path.join(dir, "index.yml"))) {
      return Response.json({ paths: [dir] });
    }

    // Case 2: parent folder — read child names, then stat each one
    let childNames: string[];
    try {
      // Plain readdirSync (no withFileTypes) avoids Dirent.isDirectory()
      // quirks with symlinks or Node.js version differences.
      childNames = fs.readdirSync(dir);
    } catch (err) {
      return Response.json(
        {
          error: "unreadable",
          detail: `Cannot read directory "${dir}": ${err instanceof Error ? err.message : String(err)}`,
        },
        { status: 400 }
      );
    }

    const scenarioPaths: string[] = [];
    for (const name of childNames) {
      const childPath = path.join(dir, name);
      try {
        const stat = fs.statSync(childPath);
        if (stat.isDirectory() && fs.existsSync(path.join(childPath, "index.yml"))) {
          scenarioPaths.push(childPath);
        }
      } catch {
        // Skip entries that can't be stat'd (broken symlinks, permission errors, etc.)
      }
    }

    if (scenarioPaths.length === 0) {
      return Response.json(
        {
          error: "no_scenarios",
          detail:
            `No scenario sub-folders (containing index.yml) were found inside "${dir}". ` +
            `Found ${childNames.length} item(s): ${childNames.slice(0, 5).join(", ")}${childNames.length > 5 ? "…" : ""}`,
        },
        { status: 404 }
      );
    }

    return Response.json({ paths: scenarioPaths });
  } catch (err) {
    return Response.json(
      {
        error: "expand_failed",
        detail: err instanceof Error ? err.message : "Unexpected error.",
      },
      { status: 500 }
    );
  }
}
