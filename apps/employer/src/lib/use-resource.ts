import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { isUnauthorized } from "../api";
import { useSession } from "../auth/session";

type ResourceState<T> =
  | { status: "loading"; data: T | undefined }
  | { status: "ready"; data: T }
  | { status: "error"; data: T | undefined; error: unknown };

export type Resource<T> = ResourceState<T> & { reload: () => void };

/**
 * Load server data for a view. Keeps the previous value during reloads (no flicker),
 * ignores responses from superseded requests, and turns a 401 into a session expiry.
 * `deps` are the inputs of `load`, exactly as for useCallback.
 */
export function useResource<T>(load: () => Promise<T>, deps: DependencyList): Resource<T> {
  const { expire } = useSession();
  const [state, setState] = useState<ResourceState<T>>({ status: "loading", data: undefined });
  const generation = useRef(0);
  const stableLoad = useCallback(load, deps);

  const run = useCallback(() => {
    const current = ++generation.current;
    setState((previous) => ({ status: "loading", data: previous.data }));
    stableLoad().then(
      (data) => {
        if (current === generation.current) setState({ status: "ready", data });
      },
      (error: unknown) => {
        if (current !== generation.current) return;
        if (isUnauthorized(error)) expire();
        setState((previous) => ({ status: "error", data: previous.data, error }));
      },
    );
  }, [stableLoad, expire]);

  useEffect(() => {
    run();
    return () => {
      generation.current++;
    };
  }, [run]);

  return { ...state, reload: run };
}
