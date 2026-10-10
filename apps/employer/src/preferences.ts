import { useSyncExternalStore } from "react";

/** Non-secret defaults for this browser only; never represent organization policy. */
export interface EmployerPreferences {
  reasoningEffort: "off" | "medium" | "max";
  submitWithEnter: boolean;
  reducedMotion: boolean;
}

const storageKey = "comit.employer.preferences.v1";
const changeEvent = "comit:employer-preferences";
const defaults: EmployerPreferences = {
  reasoningEffort: "off",
  submitWithEnter: true,
  reducedMotion: false,
};
let snapshot: EmployerPreferences | undefined;

/** Accept only known preference values from untrusted browser storage. */
function validated(value: unknown): EmployerPreferences {
  if (!value || typeof value !== "object") return { ...defaults };
  const candidate = value as Record<string, unknown>;
  return {
    reasoningEffort:
      candidate.reasoningEffort === "off" ||
      candidate.reasoningEffort === "medium" ||
      candidate.reasoningEffort === "max"
        ? candidate.reasoningEffort
        : defaults.reasoningEffort,
    submitWithEnter:
      typeof candidate.submitWithEnter === "boolean"
        ? candidate.submitWithEnter
        : defaults.submitWithEnter,
    reducedMotion:
      typeof candidate.reducedMotion === "boolean"
        ? candidate.reducedMotion
        : defaults.reducedMotion,
  };
}

function readStorage(): EmployerPreferences {
  try {
    return validated(JSON.parse(window.localStorage.getItem(storageKey) || "null"));
  } catch {
    return { ...defaults };
  }
}

function getSnapshot(): EmployerPreferences {
  snapshot ??= typeof window === "undefined" ? defaults : readStorage();
  return snapshot;
}

function subscribe(notify: () => void): () => void {
  const changed = () => notify();
  const storageChanged = (event: StorageEvent) => {
    if (event.key !== storageKey && event.key !== null) return;
    snapshot = readStorage();
    notify();
  };
  window.addEventListener(changeEvent, changed);
  window.addEventListener("storage", storageChanged);
  return () => {
    window.removeEventListener(changeEvent, changed);
    window.removeEventListener("storage", storageChanged);
  };
}

/** Update local UI defaults; blocked storage falls back to synchronized in-memory state. */
function updatePreferences(patch: Partial<EmployerPreferences>): void {
  snapshot = validated({ ...getSnapshot(), ...patch });
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(snapshot));
  } catch {
    // Private browsing/storage limits must not prevent using the current UI.
  }
  window.dispatchEvent(new CustomEvent(changeEvent));
}

/** Subscribe across mounted components and browser tabs; updates persist when storage permits. */
export function useEmployerPreferences(): {
  preferences: EmployerPreferences;
  updatePreferences: (patch: Partial<EmployerPreferences>) => void;
} {
  const preferences = useSyncExternalStore(subscribe, getSnapshot, () => defaults);
  return { preferences, updatePreferences };
}
