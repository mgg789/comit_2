/** Bounded SSE transport: no database connection survives a poll or an idle heartbeat. */
import { once } from "node:events";
import type { FastifyReply, FastifyRequest } from "fastify";

/** Domain event sequence is an opaque durable cursor, not a count of visible messages. */
export interface StreamEvent { sequence: number; type: string; [key: string]: unknown; }
/** Poll result may advance over redacted events without disclosing their metadata. */
export interface StreamPage { events: StreamEvent[]; next_sequence: number; }
/** Lifetime/poll options are bounded by the caller; test transports may use shorter intervals. */
export interface StreamOptions { lifetimeMs: number; pollMs: number; signal: AbortSignal; afterSequence: number; }

/** Parse safe cursors and require Last-Event-ID to agree with an explicit query cursor. */
export function streamCursor(header: unknown, query: string | undefined): number {
  const parse = (value: unknown) => {
    if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,15})$/.test(value)) throw new Error("invalid_cursor");
    const cursor = Number(value);
    if (!Number.isSafeInteger(cursor)) throw new Error("invalid_cursor");
    return cursor;
  };
  const fromHeader = header === undefined ? undefined : parse(header);
  const fromQuery = query === undefined ? undefined : parse(query);
  if (fromHeader !== undefined && fromQuery !== undefined && fromHeader !== fromQuery) throw new Error("cursor_conflict");
  return fromQuery ?? fromHeader ?? 0;
}

/** Stream a validated initial page; subsequent authorization or provider errors become safe close events. */
export async function serveStream(request: FastifyRequest, reply: FastifyReply, initial: StreamPage,
  poll: (after: number, signal: AbortSignal) => Promise<StreamPage>, options: StreamOptions,
  errorCode: (error: unknown) => { code: string; status: number }): Promise<void> {
  const controller = new AbortController();
  const stop = () => controller.abort();
  if (options.signal.aborted) stop();
  options.signal.addEventListener("abort", stop, { once: true });
  reply.raw.once("close", stop);
  const lifetime = setTimeout(stop, options.lifetimeMs);
  reply.hijack();
  reply.raw.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store",
    "x-content-type-options": "nosniff", "x-accel-buffering": "no", "x-request-id": request.id });
  reply.raw.flushHeaders();
  const write = async (frame: string): Promise<void> => {
    if (controller.signal.aborted || reply.raw.destroyed) return;
    if (!reply.raw.write(frame)) {
      // One bounded frame at a time; a slow client cannot accumulate pending pages in memory.
      await once(reply.raw, "drain", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(3000)]) });
    }
  };
  let cursor = options.afterSequence;
  const send = async (page: StreamPage) => {
    for (const event of page.events) await write(`id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
    if (page.next_sequence > (page.events.at(-1)?.sequence ?? cursor)) {
      await write(`id: ${page.next_sequence}\nevent: cursor\ndata: ${JSON.stringify({ type: "cursor", sequence: page.next_sequence })}\n\n`);
    }
    cursor = page.next_sequence;
  };
  try {
    await write("retry: 1000\n\n");
    await send(initial);
    while (!controller.signal.aborted) {
      await new Promise<void>(resolve => {
        const finish = () => { clearTimeout(timer); controller.signal.removeEventListener("abort", finish); resolve(); };
        const timer = setTimeout(finish, options.pollMs);
        controller.signal.addEventListener("abort", finish, { once: true });
      });
      if (controller.signal.aborted) break;
      const page = await poll(cursor, controller.signal);
      if (controller.signal.aborted) break;
      await send(page);
      await write(": heartbeat\n\n");
    }
    if (!reply.raw.destroyed) reply.raw.write(`event: reconnect\ndata: ${JSON.stringify({ after_sequence: cursor })}\n\n`);
  } catch (error) {
    if (!reply.raw.destroyed && !controller.signal.aborted) {
      const safe = errorCode(error);
      reply.raw.write(`event: error\ndata: ${JSON.stringify({ ...safe, after_sequence: cursor })}\n\n`);
    }
  } finally {
    clearTimeout(lifetime); options.signal.removeEventListener("abort", stop); reply.raw.removeListener("close", stop);
    if (!reply.raw.destroyed) reply.raw.end();
  }
}
