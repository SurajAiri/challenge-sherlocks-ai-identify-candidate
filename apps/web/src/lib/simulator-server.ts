/** Server-only: base URL of the Python simulator FastAPI service. Only
 * ever read from `app/api/simulator/*` route handlers (Node runtime),
 * never bundled into client code. */
export const SIMULATOR_BASE_URL =
  process.env.SIMULATOR_BASE_URL ?? "http://localhost:8080";
