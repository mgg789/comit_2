import type { AccountView } from "@comit/shared";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, isUnauthorized } from "../api";

type SessionState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; account: AccountView }
  | { status: "error"; retry: () => void };

interface SessionContextValue {
  state: SessionState;
  /** Store the account returned by a successful code verification (cookie is already set). */
  signedIn: (account: AccountView) => void;
  /** Revoke the current server session and drop local state. */
  signOut: () => Promise<void>;
  /** Mark the session dead after any 401 from a data call. */
  expire: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/** Resolves the employer account from the HttpOnly cookie once and shares it with the tree. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  const load = useCallback(() => {
    setState({ status: "loading" });
    api
      .getAccount("employer")
      .then((account) => setState({ status: "authenticated", account }))
      .catch((error: unknown) => {
        if (isUnauthorized(error)) setState({ status: "anonymous" });
        else setState({ status: "error", retry: load });
      });
  }, []);

  useEffect(load, [load]);

  // Stable callbacks: data hooks list `expire` as a dependency and must not re-run on session changes.
  const signedIn = useCallback((account: AccountView) => setState({ status: "authenticated", account }), []);
  const expire = useCallback(() => setState({ status: "anonymous" }), []);
  const signOut = useCallback(async () => {
    try {
      await api.logout("employer");
    } finally {
      setState({ status: "anonymous" });
    }
  }, []);

  const value = useMemo<SessionContextValue>(() => ({ state, signedIn, signOut, expire }), [state, signedIn, signOut, expire]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** Access the employer session; must be rendered under SessionProvider. */
export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}

/** Authenticated account; only valid inside the signed-in route tree. */
export function useAccount(): AccountView {
  const { state } = useSession();
  if (state.status !== "authenticated") throw new Error("useAccount requires an authenticated session");
  return state.account;
}
