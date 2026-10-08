/** Server-side FSP registry verification against the integration-engine contour. */

export interface FspPayload {
  rank: string;
  rating: number;
  events: Array<{ title: string; year: number; place: number | null }>;
  certificates: string[];
  verified_at: string;
}

export type FspVerification =
  | { ok: true; payload: FspPayload }
  | { ok: false; reason: "absent" | "unavailable" };

/** Registry base URL; the contour degrades gracefully when the engine is not configured. */
export function fspRegistryUrl(): string {
  return process.env.INTEGRATION_ENGINE_URL ?? "http://integration-engine:8103";
}

/**
 * Verify one declared FSP ID against the registry.
 * 404 is the mandatory normal state (no FSP history, TZ §2) and is reported as
 * "absent"; network/shape failures degrade to "unavailable" without throwing,
 * so a profile save never blocks on the registry.
 */
export async function verifyFsp(fspId: string): Promise<FspVerification> {
  try {
    const response = await fetch(`${fspRegistryUrl()}/fsp/participants/${encodeURIComponent(fspId)}`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 404) return { ok: false, reason: "absent" };
    if (!response.ok) return { ok: false, reason: "unavailable" };
    const body = await response.json() as Record<string, unknown>;
    if (typeof body.fsp_id !== "string" || typeof body.rank !== "string"
        || typeof body.rating !== "number" || !Number.isFinite(body.rating)
        || !Array.isArray(body.events) || !Array.isArray(body.certificates)) {
      return { ok: false, reason: "unavailable" };
    }
    const events = (body.events as Array<Record<string, unknown>>).map(event => ({
      title: String(event.title ?? ""),
      year: Number(event.year ?? 0),
      place: typeof event.place === "number" ? event.place : null,
    })).filter(event => event.title.length > 0 && Number.isFinite(event.year));
    const certificates = (body.certificates as unknown[]).map(String);
    return {
      ok: true,
      payload: {
        rank: body.rank,
        rating: Math.round(body.rating),
        events,
        certificates,
        verified_at: new Date().toISOString(),
      },
    };
  } catch {
    return { ok: false, reason: "unavailable" };
  }
}
