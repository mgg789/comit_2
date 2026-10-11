/** Indicator states reflect observed browser events, never a hiring verdict. */
export type IndicatorState = 'green' | 'yellow' | 'red';
/** Aggregate seconds sent to the authenticated supervision endpoint. */
export interface SupervisionAggregates {
  total_seconds: number;
  in_frame_seconds: number;
  focus_lost_seconds: number;
  multi_face_events: number;
  camera: 'granted' | 'denied' | 'unavailable';
}
/** Loading is a transient local state, distinct from a failed detector or declined camera. */
export type DetectorState = 'loading' | 'ready' | 'failed';
export type CameraObservation = SupervisionAggregates['camera'] | 'loading';
/** A live camera grants permission even while model startup is pending; it does not prove a face exists. */
export function cameraObservation(hasVideo: boolean, detectorState: DetectorState): CameraObservation {
  return !hasVideo ? 'denied' : detectorState === 'loading' ? 'loading' : detectorState === 'failed' ? 'unavailable' : 'granted';
}
/** Focus takes priority over fullscreen history for the rolling 30-second badge. */
export function screenIndicator(now: number, fullscreen: boolean, focused: boolean, lastExit: number, lastBlur: number): IndicatorState {
  if (!focused || now - lastBlur < 30_000) return 'red';
  if (!fullscreen || now - lastExit < 30_000) return 'yellow';
  return 'green';
}
/** Video without a successful current face detection stays yellow. */
export function faceIndicator(hasVideo: boolean, faceCount: number): IndicatorState {
  return !hasVideo ? 'red' : faceCount > 0 ? 'green' : 'yellow';
}
/** Monotonic counters attribute each elapsed interval to the preceding observation. */
export class ProctoringCounters {
  private last: number;
  private total = 0;
  private inFrame = 0;
  private unfocused = 0;
  private multi = 0;
  private previousFaces = 0;
  private previousFocused = true;
  private camera: SupervisionAggregates['camera'] = 'denied';
  private cameraLimitation: 'denied' | 'unavailable' | null = null;
  constructor(now: number, initialReport?: SupervisionAggregates | null) {
    this.last = now;
    if (!initialReport) return;
    const { total_seconds, in_frame_seconds, focus_lost_seconds, multi_face_events, camera } = initialReport;
    if (![total_seconds, in_frame_seconds, focus_lost_seconds, multi_face_events].every(v => Number.isFinite(v) && v >= 0)
      || total_seconds > 86_400 || in_frame_seconds > total_seconds || focus_lost_seconds > total_seconds
      || !Number.isSafeInteger(multi_face_events) || multi_face_events > 10_000
      || !['granted', 'denied', 'unavailable'].includes(camera)) throw new Error('invalid_initial_supervision');
    this.total = total_seconds; this.inFrame = in_frame_seconds; this.unfocused = focus_lost_seconds;
    this.multi = multi_face_events; this.camera = camera;
    if (total_seconds > 0 && camera !== 'granted') this.cameraLimitation = camera;
  }
  /** Capture elapsed time before replacing the current state; multi-face events count entries. */
  observe(now: number, faces: number, focused: boolean, camera: CameraObservation): void {
    const elapsed = Math.max(0, now - this.last) / 1000;
    this.total += elapsed;
    if (this.previousFaces > 0) this.inFrame += elapsed;
    if (!this.previousFocused) this.unfocused += elapsed;
    if (faces > 1 && this.previousFaces <= 1) this.multi += 1;
    this.last = now;
    this.previousFaces = faces;
    this.previousFocused = focused;
    if (camera !== 'granted' && camera !== 'loading' && elapsed > 0) this.cameraLimitation ??= camera;
    this.camera = this.cameraLimitation ?? (camera === 'loading' ? 'granted' : camera);
  }
  /** Return bounded seconds; a detector failure is unknown rather than a low-presence verdict. */
  snapshot(): SupervisionAggregates {
    return { total_seconds: this.total, in_frame_seconds: Math.min(this.total, this.inFrame),
      focus_lost_seconds: Math.min(this.total, this.unfocused), multi_face_events: this.multi, camera: this.camera };
  }
}
/** Ordered media queue keeps failed chunks for explicit retry and bounds memory. */
export class MediaUploadQueue {
  private chunks: Array<{ kind: 'screen' | 'webcam'; blob: Blob; sequence: number }> = [];
  private next = 0;
  private bytes = 0;
  private running: Promise<void> | null = null;
  constructor(private readonly upload: (kind: 'screen' | 'webcam', blob: Blob, sequence: number) => Promise<void>, private readonly maximumBytes = 24 * 1024 * 1024, initialSequence = 0) {
    if (!Number.isSafeInteger(initialSequence) || initialSequence < 0) throw new Error('invalid_media_sequence');
    this.next = initialSequence;
  }
  /** Enqueue a chunk without losing it on a failed network request. */
  add(kind: 'screen' | 'webcam', blob: Blob): void {
    for (let offset = 0; offset < blob.size; offset += 2 * 1024 * 1024) {
      const part = blob.slice(offset, offset + 2 * 1024 * 1024, blob.type);
      this.chunks.push({ kind, blob: part, sequence: this.next++ }); this.bytes += part.size;
    }
    // Keep the crossing chunk, then pause producers; never silently discard video.
    if (this.bytes > this.maximumBytes) throw new Error('Запись не успевает отправляться. Проверьте соединение.');
  }
  /** Drain serially; rejected uploads remain at the head with the same idempotency sequence. */
  async drain(): Promise<void> {
    if (this.running) return this.running;
    this.running = (async () => {
      while (this.chunks.length) {
        const item = this.chunks[0]!;
        await this.upload(item.kind, item.blob, item.sequence);
        this.chunks.shift(); this.bytes -= item.blob.size;
      }
    })();
    try { await this.running; } finally { this.running = null; }
  }
}
