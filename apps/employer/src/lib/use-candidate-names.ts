import { useEffect, useState } from "react";
import { api } from "../api";

/** Session-wide cache: public display names never include contacts, so caching them is safe. */
const cache = new Map<string, string>();
const inflight = new Map<string, Promise<string | null>>();

function resolve(candidateId: string): Promise<string | null> {
  const known = cache.get(candidateId);
  if (known) return Promise.resolve(known);
  let pending = inflight.get(candidateId);
  if (!pending) {
    pending = api
      .getPublicCandidate(candidateId)
      .then((detail) => {
        cache.set(candidateId, detail.display_name);
        return detail.display_name;
      })
      .catch(() => null) // Unpublished or withdrawn profiles keep the generic label.
      .finally(() => inflight.delete(candidateId));
    inflight.set(candidateId, pending);
  }
  return pending;
}

function cachedEntries(ids: readonly string[]): Array<[string, string]> {
  return ids.flatMap((id): Array<[string, string]> => {
    const name = cache.get(id);
    return name ? [[id, name]] : [];
  });
}

/**
 * Display names for recruitment rows, which carry only candidate IDs.
 * Resolves through the public profile projection; unknown IDs map to undefined until loaded.
 */
export function useCandidateNames(candidateIds: readonly string[]): ReadonlyMap<string, string> {
  const [names, setNames] = useState<ReadonlyMap<string, string>>(() => new Map(cachedEntries(candidateIds)));
  const key = [...new Set(candidateIds)].sort().join(",");

  useEffect(() => {
    let cancelled = false;
    const ids = key ? key.split(",") : [];
    void Promise.all(ids.map(resolve)).then(() => {
      if (cancelled) return;
      setNames(new Map(cachedEntries(ids)));
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return names;
}
