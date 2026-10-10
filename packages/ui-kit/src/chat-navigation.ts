/** Preserve an internal caller in chat deep links, including after a page reload. */
export function withChatOrigin(target: string, current: string, fallback: string): string {
  if (!target.startsWith("/chat/") || new URLSearchParams(target.split("?")[1]).has("from")) return target;
  const origin = current.split("?")[0]?.startsWith("/chat/") ? chatReturnRoute(current, fallback) : safeOrigin(current, fallback);
  return `${target}${target.includes("?") ? "&" : "?"}from=${encodeURIComponent(origin)}`;
}

/** Return only a local non-conversation route; external URLs and recursive chat origins fall back. */
export function chatReturnRoute(route: string, fallback: string): string {
  return safeOrigin(new URLSearchParams(route.slice(route.indexOf("?") + 1)).get("from") || "", fallback);
}

function safeOrigin(value: string, fallback: string): string {
  return value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/chat/") && !/[\\#\u0000-\u001f]/.test(value) ? value : fallback;
}
