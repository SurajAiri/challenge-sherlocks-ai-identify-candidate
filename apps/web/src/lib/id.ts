/** Small dependency-free id generator - we don't need cryptographic
 * uniqueness, just something stable enough for React keys and local
 * storage lookups. */
export function makeId(prefix = "id"): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${rand}`;
}
