/// <reference types="vite/client" />
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FaceDetector } from '@mediapipe/tasks-vision';
import { faceIndicator, screenIndicator, cameraObservation, ProctoringCounters, MediaUploadQueue, type SupervisionAggregates, type DetectorState } from './proctoring';

/** Start options are supplied only after durable session consent has been accepted. */
export interface MediaStartOptions {
  recordingEnabled: boolean;
  webcamRecordingAllowed?: boolean;
  initialSequence?: number;
  initialReport?: SupervisionAggregates | null;
  uploadChunk: (kind: 'screen' | 'webcam', blob: Blob, sequence: number) => Promise<void>;
}
const assetBase = import.meta.env.BASE_URL;
const recorderMimeType = () => ['video/webm;codecs=vp8', 'video/webm', 'video/mp4'].find(t => MediaRecorder.isTypeSupported(t));
/** Browser capture lifecycle: previews before start; recording and counters only after start. */
export function useTestingMedia() {
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [screenStream, setScreenStream] = useState<MediaStream | null>(null);
  const [faceCount, setFaceCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [detectorError, setDetectorError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const camera = useRef<MediaStream | null>(null), screen = useRef<MediaStream | null>(null);
  const faces = useRef(0), detectorState = useRef<DetectorState>('loading'), detector = useRef<FaceDetector | null>(null);
  const counters = useRef<ProctoringCounters | null>(null);
  const queue = useRef<MediaUploadQueue | null>(null);
  const recorders = useRef<MediaRecorder[]>([]);
  const recordingFailure = useRef<string | null>(null);
  const lastReport = useRef<SupervisionAggregates | null>(null);
  const attachRecorder = useRef<((kind: 'screen' | 'webcam', stream: MediaStream) => void) | null>(null);
  const lastExit = useRef(-Infinity), lastBlur = useRef(-Infinity);
  const focused = useRef(document.hasFocus() && !document.hidden);
  const generation = useRef(0);
  const observe = useCallback(() => counters.current?.observe(performance.now(), faces.current, focused.current,
    cameraObservation(camera.current?.getVideoTracks().some(t => t.readyState === 'live' && !t.muted) ?? false, detectorState.current)), []);

  useEffect(() => {
    const visibility = () => {
      observe(); focused.current = document.hasFocus() && !document.hidden;
      if (!focused.current) lastBlur.current = performance.now(); observe(); setTick(t => t + 1);
    };
    const fullscreen = () => { if (!document.fullscreenElement) lastExit.current = performance.now(); setTick(t => t + 1); };
    window.addEventListener('blur', visibility); window.addEventListener('focus', visibility);
    document.addEventListener('visibilitychange', visibility); document.addEventListener('fullscreenchange', fullscreen);
    const timer = window.setInterval(() => { observe(); setTick(t => t + 1); }, 500);
    return () => { clearInterval(timer); window.removeEventListener('blur', visibility); window.removeEventListener('focus', visibility);
      document.removeEventListener('visibilitychange', visibility); document.removeEventListener('fullscreenchange', fullscreen); };
  }, [observe]);

  useEffect(() => {
    if (!cameraStream) return;
    let cancelled = false, timer: ReturnType<typeof setTimeout> | undefined;
    const video = document.createElement('video'); video.muted = true; video.playsInline = true; video.srcObject = cameraStream;
    const initialize = async () => {
      try {
        await video.play();
        const { FaceDetector, FilesetResolver } = await import('@mediapipe/tasks-vision');
        const files = await FilesetResolver.forVisionTasks(`${assetBase}mediapipe`);
        const model = await FaceDetector.createFromOptions(files, { baseOptions: { modelAssetPath: `${assetBase}models/blaze_face_short_range.tflite` }, runningMode: 'VIDEO', minDetectionConfidence: 0.6 });
        if (cancelled) { model.close(); return; }
        detector.current = model; detectorState.current = 'ready'; setDetectorError(null);
        const detect = () => {
          if (cancelled) return;
          try {
            const count = video.readyState >= 2 && cameraStream.getVideoTracks().some(t => t.readyState === 'live' && !t.muted)
              ? model.detectForVideo(video, performance.now()).detections.length : 0;
            observe(); faces.current = count; setFaceCount(count); observe();
          } catch { detectorState.current = 'failed'; faces.current = 0; setFaceCount(0); setDetectorError('Не удалось определить присутствие лица. Результат видеопроверки будет «не определён».'); }
          timer = setTimeout(detect, 1000);
        };
        detect();
      } catch { if (!cancelled) { detectorState.current = 'failed'; setDetectorError('Проверка лица недоступна. Можно продолжить без видеоподтверждения.'); } }
    };
    void initialize();
    return () => { cancelled = true; clearTimeout(timer); detector.current?.close(); detector.current = null; detectorState.current = 'loading';
      faces.current = 0; video.pause(); video.srcObject = null; };
  }, [cameraStream, observe]);

  const requestFullscreen = useCallback(async () => {
    try { await document.documentElement.requestFullscreen(); setError(null); }
    catch { setError('Браузер не включил полноэкранный режим. Нажмите кнопку ещё раз.'); }
  }, []);
  const requestCamera = useCallback(async () => {
    const current = generation.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15, max: 15 } }, audio: false });
      if (current !== generation.current) { stream.getTracks().forEach(t => t.stop()); return; }
      camera.current?.getTracks().forEach(t => t.stop()); faces.current = 0; detectorState.current = 'loading'; setFaceCount(0); camera.current = stream; setCameraStream(stream); attachRecorder.current?.('webcam', stream); setError(null);
      stream.getVideoTracks().forEach(t => t.addEventListener('ended', () => { if (camera.current !== stream) return; observe(); faces.current = 0; setFaceCount(0); setCameraStream(null); observe(); }));
    } catch { if (current !== generation.current) return; setError('Камера недоступна. Разрешите доступ в браузере или продолжите без камеры.'); }
  }, [observe]);
  const requestScreen = useCallback(async () => {
    const current = generation.current;
    if (!navigator.mediaDevices?.getDisplayMedia) { setError('Этот браузер не поддерживает показ экрана. Откройте тест на компьютере в Chrome или Firefox.'); return; }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 5, max: 10 } }, audio: false });
      if (current !== generation.current) { stream.getTracks().forEach(t => t.stop()); return; }
      if (stream.getVideoTracks()[0]?.getSettings().displaySurface !== 'monitor') {
        stream.getTracks().forEach(t => t.stop()); setError('Выберите весь экран, а не отдельное окно или вкладку.'); return;
      }
      screen.current?.getTracks().forEach(t => t.stop()); screen.current = stream; setScreenStream(stream); attachRecorder.current?.('screen', stream); setError(null);
      stream.getVideoTracks().forEach(t => t.addEventListener('ended', () => { if (screen.current !== stream) return; setScreenStream(null); setError('Доступ к экрану прекращён. Возобновите показ экрана.'); }));
    } catch { if (current !== generation.current) return; setError('Чтобы начать тест, разрешите показ всего экрана.'); }
  }, []);
  const retryUploads = useCallback(async () => {
    try { await queue.current?.drain(); if (recordingFailure.current) throw new Error(recordingFailure.current);
      recorders.current.forEach(r => { if (r.state === 'paused') r.resume(); }); setUploadError(null); } catch { setUploadError('Запись не отправлена. Проверьте соединение и повторите отправку.'); throw new Error('media_upload_failed'); }
  }, []);
  /** Validate browser recording support before the server counts a started attempt; no recorder is started here. */
  const validateReadiness = useCallback((options: Pick<MediaStartOptions, 'recordingEnabled' | 'webcamRecordingAllowed'>) => {
    if (!screen.current?.getVideoTracks().some(t => t.readyState === 'live')) throw new Error('Нужен доступ к экрану.');
    if (!options.recordingEnabled) return;
    if (typeof MediaRecorder === 'undefined') throw new Error('Этот браузер не поддерживает запись. Используйте Chrome или Firefox.');
    const mimeType = recorderMimeType();
    const settings = mimeType ? { mimeType } : {};
    // Construction validates the codec/stream combination without collecting a frame.
    new MediaRecorder(screen.current, settings);
    if (options.webcamRecordingAllowed && camera.current?.getVideoTracks().some(t => t.readyState === 'live')) new MediaRecorder(camera.current, settings);
  }, []);
  const start = useCallback((options: MediaStartOptions) => {
    if (counters.current) throw new Error('Тест уже запущен.');
    validateReadiness(options);
    if (!screen.current?.getVideoTracks().some(t => t.readyState === 'live')) throw new Error('Нужен доступ к экрану.');
    const streams: Array<['screen' | 'webcam', MediaStream]> = [['screen', screen.current]];
    if (options.webcamRecordingAllowed && camera.current?.getVideoTracks().some(t => t.readyState === 'live')) streams.push(['webcam', camera.current]);
    const prepared: MediaRecorder[] = [];
    if (options.recordingEnabled) {
      if (typeof MediaRecorder === 'undefined') throw new Error('Этот браузер не поддерживает запись. Используйте Chrome или Firefox.');
      queue.current = new MediaUploadQueue(options.uploadChunk, undefined, options.initialSequence);
      const mimeType = recorderMimeType();
      const createRecorder = (kind: 'screen' | 'webcam', stream: MediaStream) => {
        const recorder = new MediaRecorder(stream, { ...(mimeType ? { mimeType } : {}), videoBitsPerSecond: kind === 'screen' ? 350_000 : 200_000 });
        recorder.ondataavailable = event => {
          if (!event.data.size) return;
          try { queue.current!.add(kind, event.data); void retryUploads().catch(() => {}); }
          catch (reason) { setUploadError(reason instanceof Error ? reason.message : 'Не удалось сохранить запись.'); prepared.forEach(r => { if (r.state === 'recording') r.pause(); }); }
        };
        recorder.onerror = () => { recordingFailure.current = 'Запись прервалась. Проверьте доступ к экрану и камере.'; setUploadError(recordingFailure.current); };
        prepared.push(recorder); return recorder;
      };
      for (const [kind, stream] of streams) createRecorder(kind, stream);
      attachRecorder.current = (kind, stream) => { if (kind === 'webcam' && !options.webcamRecordingAllowed) return; try { createRecorder(kind, stream).start(5000); } catch { recordingFailure.current = 'Не удалось возобновить запись.'; setUploadError(recordingFailure.current); } };
      try { prepared.forEach(r => r.start(5000)); } catch (reason) { prepared.forEach(r => { if (r.state !== 'inactive') r.stop(); }); throw reason; }
    }
    recorders.current = prepared; counters.current = new ProctoringCounters(performance.now(), options.initialReport); observe();
  }, [observe, retryUploads, validateReadiness]);
  /** Cumulative checkpoint includes the previous browser run and is safe to persist repeatedly. */
  const snapshot = useCallback((): SupervisionAggregates => {
    observe();
    return counters.current?.snapshot() ?? lastReport.current ?? { total_seconds: 0, in_frame_seconds: 0, focus_lost_seconds: 0, multi_face_events: 0, camera: 'denied' };
  }, [observe]);
  const finish = useCallback(async (): Promise<SupervisionAggregates> => {
    observe();
    if (detectorState.current === 'loading' && camera.current?.getVideoTracks().some(t => t.readyState === 'live')) counters.current?.observe(performance.now(), 0, focused.current, 'unavailable');
    lastReport.current = counters.current?.snapshot() ?? lastReport.current; counters.current = null; attachRecorder.current = null;
    await Promise.all(recorders.current.map(recorder => recorder.state === 'inactive' ? Promise.resolve() : new Promise<void>(resolve => {
      recorder.addEventListener('stop', () => resolve(), { once: true }); recorder.stop();
    })));
    recorders.current = []; await retryUploads(); observe();
    return lastReport.current ?? { total_seconds: 0, in_frame_seconds: 0, focus_lost_seconds: 0, multi_face_events: 0, camera: 'denied' };
  }, [observe, retryUploads]);
  const stop = useCallback(() => {
    generation.current += 1; attachRecorder.current = null;
    recorders.current.forEach(r => { if (r.state !== 'inactive') r.stop(); }); recorders.current = [];
    camera.current?.getTracks().forEach(t => t.stop()); screen.current?.getTracks().forEach(t => t.stop());
    camera.current = null; screen.current = null; if (document.fullscreenElement === document.documentElement) void document.exitFullscreen().catch(() => {}); setCameraStream(null); setScreenStream(null); faces.current = 0; setFaceCount(0);
  }, []);
  useEffect(() => () => { generation.current += 1; recorders.current.forEach(r => { if (r.state !== 'inactive') r.stop(); });
    camera.current?.getTracks().forEach(t => t.stop()); screen.current?.getTracks().forEach(t => t.stop()); }, []);
  void tick;
  const hasCamera = cameraStream?.getVideoTracks().some(t => t.readyState === 'live' && !t.muted) ?? false;
  return { cameraStream, screenStream, faceState: faceIndicator(hasCamera, faceCount), screenState: screenIndicator(performance.now(), !!document.fullscreenElement, focused.current, lastExit.current, lastBlur.current),
    error, detectorError, uploadError, requestCamera, requestScreen, requestFullscreen, validateReadiness, snapshot, start, finish, retryUploads, stop, dispose: stop };
}
