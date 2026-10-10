import { createContext, useContext, useRef, useSyncExternalStore, type Dispatch, type ReactNode, type SetStateAction } from "react";

const Scope = createContext("guest");
const prefix = "comit.ui-state.v1.";
const eventName = "comit:ui-state";
const snapshots = new Map<string, unknown>();

/** Separate non-secret browser drafts by role and authenticated account; never grants server authority. */
export function PersistentStateScope({ scope, children }: { scope: string; children: ReactNode }) {
  return <Scope.Provider value={scope}>{children}</Scope.Provider>;
}

/** Reject oversized, non-JSON and prototype-bearing local values before restoring a UI draft. */
export function validUiState(value: unknown, depth = 0): boolean {
  if (depth > 16) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= 128000;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 2000 && value.every(item => validUiState(item, depth + 1));
  if (typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return false;
  return Object.entries(value).every(([key, item]) => !["__proto__", "prototype", "constructor"].includes(key) && validUiState(item, depth + 1));
}

/** Remove a confirmed form draft (or its named fields) without touching other account drafts. */
export function clearPersistentState(scope: string, keyPrefix: string): void {
  const start = `${prefix}${encodeURIComponent(scope)}.${keyPrefix}`;
  const keys = new Set([...snapshots.keys()].filter(key => key.startsWith(start)));
  try { for (let i = 0; i < localStorage.length; i++) { const key = localStorage.key(i); if (key?.startsWith(start)) keys.add(key); } } catch { /* Storage can be disabled. */ }
  for (const key of keys) { snapshots.delete(key); try { localStorage.removeItem(key); } catch { /* Keep the current UI usable. */ } }
  window.dispatchEvent(new CustomEvent(eventName));
}

/** Persist a controlled non-secret input/filter. Use revision-specific keys for server-backed drafts and an exact validator for structured fields. */
export function usePersistentState<T>(key: string, initial: T | (() => T), validate?: (value: unknown) => value is T): [T, Dispatch<SetStateAction<T>>] {
  const scope = useContext(Scope);
  const storageKey = `${prefix}${encodeURIComponent(scope)}.${key}`;
  const fallback = useRef<{ key: string; value: T } | null>(null);
  if (!fallback.current || fallback.current.key !== storageKey) fallback.current = { key: storageKey, value: typeof initial === "function" ? (initial as () => T)() : initial };
  const defaultValue = fallback.current.value;
  const acceptable = (value: unknown): value is T => validUiState(value) && (validate ? validate(value) : (Array.isArray(defaultValue) ? Array.isArray(value) : defaultValue === null ? value === null : typeof value === typeof defaultValue));
  const read = (): T => {
    const cached = snapshots.get(storageKey);
    if (snapshots.has(storageKey) && acceptable(cached)) return cached;
    let value: T = defaultValue;
    try { const raw = localStorage.getItem(storageKey); if (raw && raw.length <= 128000) { const parsed: unknown = JSON.parse(raw); if (acceptable(parsed)) value = parsed; } } catch { /* Corrupt/blocked storage falls back to current defaults. */ }
    snapshots.set(storageKey, value); return value;
  };
  const subscribe = (notify: () => void) => {
    const changed = () => notify();
    const external = (event: StorageEvent) => { if (event.key === storageKey || event.key === null) { snapshots.delete(storageKey); notify(); } };
    window.addEventListener(eventName, changed); window.addEventListener("storage", external);
    return () => { window.removeEventListener(eventName, changed); window.removeEventListener("storage", external); };
  };
  const value = useSyncExternalStore(subscribe, read, () => defaultValue);
  const setValue: Dispatch<SetStateAction<T>> = next => {
    const resolved = typeof next === "function" ? (next as (prior: T) => T)(read()) : next;
    snapshots.set(storageKey, resolved);
    try { const serialized = JSON.stringify(resolved); if (serialized.length <= 128000) localStorage.setItem(storageKey, serialized); } catch { /* The in-memory draft survives navigation if storage is blocked. */ }
    window.dispatchEvent(new CustomEvent(eventName));
  };
  return [value, setValue];
}

/** Read the current role/account namespace for revision-bound draft cleanup. */
export function usePersistenceScope(): string { return useContext(Scope); }

/** Clear only named drafts in the currently authenticated account namespace. */
export function useClearPersistentState(): (keyPrefix: string) => void {
  const scope = useContext(Scope);
  return key => clearPersistentState(scope, key);
}
