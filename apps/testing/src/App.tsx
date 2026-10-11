import { ApiError, createApiClient } from "@comit/api-client";
import type { TestingNextTask, TestingPreflight, TestingResult } from "@comit/shared";
import { Dialog } from "@comit/ui-kit/app-shell";
import { Action, Checkbox, Select, State } from "@comit/ui-kit/design";
import { useEffect, useRef, useState } from "react";
import { useTestingMedia } from "./media";
import { MonitoringGlyph } from "./monitoring-glyph";
import { answerReady, grades, mediaBase64, readSubject, retryDate, subjects, testingError } from "./runtime";

const api = createApiClient();
const base = import.meta.env.BASE_URL;
/** A real stream preview; detached video elements release their stream references on unmount. */
function Preview({ stream, label }: { stream: MediaStream | null; label: string }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (video.current) video.current.srcObject = stream; return () => { if (video.current) video.current.srcObject = null; }; }, [stream]);
  return <div className="testing-preview"><h2>{label}</h2>{stream ? <video ref={video} autoPlay muted playsInline aria-label={label} /> : <p>Предпросмотр появится после разрешения доступа</p>}</div>;
}

/** Candidate-owned testing lifecycle. No attempt exists before explicit start and consent. */
export default function App() {
  const params = new URLSearchParams(window.location.search);
  const [subject, setSubject] = useState(() => readSubject(params.get("subject")));
  const mode = params.get("mode") === "upgrade" ? "upgrade" : "retake";
  const requestedGrade = params.get("grade");
  const declaredGrade = requestedGrade && Object.hasOwn(grades, requestedGrade) ? requestedGrade as keyof typeof grades : undefined;
  const [phase, setPhase] = useState<"confirm" | "setup" | "test" | "finishing" | "result">(params.get("ready") === "1" ? "setup" : "confirm");
  const [preflight, setPreflight] = useState<TestingPreflight | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState<TestingNextTask | null>(null);
  const [answer, setAnswer] = useState<string | number | null>(null);
  const [result, setResult] = useState<TestingResult | null>(null);
  const [unauthenticated, setUnauthenticated] = useState(false);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [screenConsent, setScreenConsent] = useState(false), [cameraConsent, setCameraConsent] = useState(false);
  const [withoutCamera, setWithoutCamera] = useState(false), [cameraWarning, setCameraWarning] = useState(false), [rules, setRules] = useState(false), [exitWarning, setExitWarning] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null), [seconds, setSeconds] = useState(0);
  const media = useTestingMedia();
  const phaseRef = useRef(phase); phaseRef.current = phase;
  const operation = useRef(false);
  const terminalIntent = useRef<"finish" | "abandon">("finish");
  const supervisionQueue = useRef<Promise<unknown>>(Promise.resolve());
  const screenLive = !!media.screenStream?.getVideoTracks().some(track => track.readyState === "live");
  const cameraLive = !!media.cameraStream?.getVideoTracks().some(track => track.readyState === "live");
  const recordingEnabled = preflight?.active_session_id ? preflight.active_recording_enabled : preflight?.recording_enabled;
  const title = `Грейд-тест${declaredGrade ? ` · ${grades[declaredGrade]}` : ""} · ${subjects[subject]}`;
  async function run(action: () => Promise<void>) { if (operation.current) return; operation.current = true; setBusy(true); setError(null); try { await action(); } catch (reason) { setError(testingError(reason)); } finally { operation.current = false; setBusy(false); } }
  async function refresh() { setLoading(true); setError(null); try { setPreflight(await api.testingPreflight(subject, mode)); } catch (reason) { setUnauthenticated(reason instanceof ApiError && reason.status === 401); setError(testingError(reason)); } finally { setLoading(false); } }
  useEffect(() => { let live = true; setLoading(true); setPreflight(null); api.testingPreflight(subject, mode).then(value => { if (live) setPreflight(value); }).catch(reason => { if (live) { setUnauthenticated(reason instanceof ApiError && reason.status === 401); setError(testingError(reason)); } }).finally(() => { if (live) setLoading(false); }); return () => { live = false; }; }, [subject, declaredGrade, mode]);
  useEffect(() => { if (!startedAt) return; const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000); return () => window.clearInterval(id); }, [startedAt]);
  useEffect(() => { const protect = (event: BeforeUnloadEvent) => { if (phaseRef.current === "test" || phaseRef.current === "finishing") { event.preventDefault(); } }; window.addEventListener("beforeunload", protect); return () => window.removeEventListener("beforeunload", protect); }, []);
  useEffect(() => {
    if (phase !== "test" || !sessionId) return;
    const timer = window.setInterval(() => {
      const report = media.snapshot();
      supervisionQueue.current = supervisionQueue.current.catch(() => {}).then(() => api.testingSupervision(sessionId, report));
      void supervisionQueue.current.catch(() => setError("Не удалось сохранить данные прохождения. Проверьте подключение: сохранение повторится автоматически."));
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [phase, sessionId, media.snapshot]);
  function back() { media.stop(); if (document.fullscreenElement) void document.exitFullscreen().catch(() => {}); const url = new URL("/candidate/#/resume", window.location.origin); if (import.meta.env.DEV && window.location.port === "5175") url.port = "5173"; window.location.assign(url.href); }
  async function complete(id: string) {
    setPhase("finishing");
    await supervisionQueue.current.catch(() => {});
    const previous = await api.testingResult(id);
    if (previous.status === "abandoned") { setResult(previous); setPhase("result"); media.stop(); return; }
    if (!previous.capture_finalized) {
      const aggregates = await media.finish();
      await api.testingSupervision(id, aggregates);
    }
    if (terminalIntent.current === "abandon") { await api.abandonTestingSession(id); setResult(await api.testingResult(id)); }
    else setResult(await api.finishTestingSession(id));
    setPhase("result");
    media.stop(); if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
  }
  async function loadNext(id: string) {
    try {
      const next = await api.nextTestingTask(id); setQuestion(next); setAnswer(null);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        const current = await api.testingResult(id);
        if (current.status === "finished") { await complete(id); return; }
      }
      throw reason;
    }
  }
  async function begin() {
    if (!screenLive || !screenConsent || (!withoutCamera && (!cameraLive || !cameraConsent))) return;
    await run(async () => {
      const current = await api.testingPreflight(subject, mode);
      setPreflight(current);
      const existing = current.active_session_id;
      const mediaOptions = { recordingEnabled: existing ? current.active_recording_enabled : current.recording_enabled,
        webcamRecordingAllowed: cameraLive && cameraConsent && (!existing || current.active_session_consents?.video_analysis === true) };
      media.validateReadiness(mediaOptions);
      const session = existing ? { session_id: existing } : await api.startTestingSession({ specialization: subject, mode, ...(declaredGrade ? { declared_grade: declaredGrade } : {}), consents: { version: "testing-media-v1", processing: true, screen_recording: mediaOptions.recordingEnabled, video_analysis: cameraLive && cameraConsent } });
      setSessionId(session.session_id);
      setPreflight(value => value ? { ...value, active_session_id: session.session_id } : value);
      media.start({ ...mediaOptions, initialSequence: current.next_sequence, initialReport: current.active_supervision, uploadChunk: async (kind, blob, sequence) => { await api.uploadTestingMedia(session.session_id, { kind, data_base64: await mediaBase64(blob), sequence, mime_type: blob.type }); } });
      setStartedAt(Date.now()); setPhase("test"); await loadNext(session.session_id);
    });
  }
  async function submit() {
    if (!sessionId || !question || !answerReady(question.task, answer) || !screenLive || media.uploadError) return;
    await run(async () => {
      const receipt = await api.answerTestingTask(sessionId, { item_id: question.task.item_id, answer: answer! });
      setQuestion(null); setAnswer(null);
      if (receipt.status === "finished") await complete(sessionId); else await loadNext(sessionId);
    });
  }
  return <main className="comit-design testing-app">
    <div className="testing-art" aria-hidden="true"><img className="testing-band-one" src={`${base}design/outer-band-left.png`} alt="" /><img className="testing-band-two" src={`${base}design/outer-band-right.png`} alt="" /></div>
    <section className={`testing-shell ${phase === "test" ? "testing-running" : ""}`}>
      <div className="testing-panel-art" aria-hidden="true" />
      <h1>{title}</h1>
      {(phase === "confirm" || phase === "setup") && <>
        {loading ? <State loading /> : error && !preflight ? <><State error={error} retry={() => void refresh()} />{unauthenticated && <Action onClick={back}>Войти как кандидат</Action>}</> : <>
          {phase === "confirm" && <section className="testing-introduction"><p>Подтвердите уровень знаний по выбранному направлению.</p><Select aria-label="Направление теста" value={subject} onChange={event => setSubject(readSubject(event.target.value))}>{Object.entries(subjects).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</Select>{preflight?.eligible || preflight?.active_session_id ? <Action variant="primary" onClick={() => { void media.requestFullscreen(); setPhase("setup"); }}>Подготовиться к тесту</Action> : <p>Следующее прохождение доступно {retryDate(preflight?.next_attempt_at ?? null)}.</p>}<Action onClick={back}>Вернуться в резюме</Action></section>}
          {phase === "setup" && <>
            {preflight && !preflight.eligible && !preflight.active_session_id && <p className="testing-notice">Следующее прохождение доступно {retryDate(preflight.next_attempt_at)}.</p>}
            {preflight?.active_session_id && <p className="testing-notice">У вас есть начатая попытка. Продолжите её после подключения экрана.</p>}
            <div className="testing-preview-grid"><div><Preview stream={media.cameraStream} label="Камера (с распознаванием лиц)" /><div className="testing-preview-actions"><Action onClick={() => { setWithoutCamera(false); void media.requestCamera(); }}>Включить камеру</Action><Action className="testing-danger" onClick={() => setCameraWarning(true)}>Без камеры</Action></div></div><div><Preview stream={media.screenStream} label="Видео с экрана" /><div className="testing-preview-actions"><Action onClick={() => void media.requestScreen()}>Поделиться всем экраном</Action><Action onClick={() => setRules(true)}>Правила тестирования</Action></div></div></div>
            {preflight && !recordingEnabled && <p className="testing-notice">В этом тесте видео не сохраняется. Платформа учитывает присутствие в кадре и переключения окна.</p>}
            <div className="testing-consents"><label><Checkbox checked={screenConsent} onChange={event => setScreenConsent(event.target.checked)} />{recordingEnabled ? "Я разрешаю запись и хранение экрана в течение 90 дней для проверки честности тестирования." : "Я разрешаю показ экрана и хранение сведений о прохождении теста в течение 90 дней. Видео не записывается."}</label>{!withoutCamera && <label><Checkbox checked={cameraConsent} onChange={event => setCameraConsent(event.target.checked)} />{recordingEnabled ? "Я разрешаю запись и хранение видео с веб-камеры в течение 90 дней для проверки честности тестирования." : "Я разрешаю определение присутствия в кадре на моём устройстве и хранение результата в течение 90 дней. Видео не записывается."}</label>}<button className="testing-legal" onClick={() => setRules(true)}>Полное соглашение и правила</button></div>
            <p className="testing-safe-exit">Вы можете вернуться без потери попытки. После нажатия «Начать» попытка будет учитываться.</p>
            {media.screenState !== "green" && <Action onClick={() => void media.requestFullscreen()}>Включить полный экран</Action>}
            <div className="testing-footer"><Action onClick={back}>Вернуться</Action><Action variant="primary" disabled={busy || !screenLive || !screenConsent || (!withoutCamera && (!cameraLive || !cameraConsent)) || (!preflight?.eligible && !preflight?.active_session_id) || !document.fullscreenElement} onClick={() => void begin()}>{busy ? "Начинаем…" : preflight?.active_session_id ? "Продолжить попытку" : "Начать"}</Action></div>
          </>}
        </>}
      </>}
      {phase === "test" && <>
        <section className={`testing-question glass ${question?.task.type === "code" ? "testing-code" : ""}`}>
          {question ? <><h2>Задание {question.answered + 1} · {subjects[subject]}. {question.task.prompt}</h2>{question.task.type === "mcq" ? <div className="testing-answers" role="radiogroup" aria-label="Выберите ответ">{question.task.options?.map((option, index) => <label key={index} className={answer === index ? "selected" : ""}><input type="radio" name="answer" checked={answer === index} onChange={() => setAnswer(index)} disabled={busy} /><span>{option}</span></label>)}</div> : <div className="testing-written">{question.task.type === "code" && <header><span>solution.py</span><span>Python 3 · {question.task.function_name}</span></header>}<textarea aria-label={question.task.type === "code" ? "Код решения на Python" : "Введите ответ"} placeholder={question.task.type === "code" ? `def ${question.task.function_name ?? "solution"}(...):\n    # Ваше решение` : "Введите ответ"} spellCheck={question.task.type !== "code"} value={typeof answer === "string" ? answer : ""} maxLength={16000} onChange={event => setAnswer(event.target.value)} disabled={busy} /></div>}<Action className="testing-continue" disabled={busy || !answerReady(question.task, answer) || !screenLive || !!media.uploadError} onClick={() => void submit()}>{busy ? "Сохраняем ответ…" : "Продолжить"}</Action><progress value={question.answered} max={preflight?.max_questions ?? 30} aria-label="Отвечено вопросов" /></> : <State loading={busy} error={error ?? undefined} retry={sessionId ? () => void run(() => loadNext(sessionId)) : undefined} />}
        </section>
        <aside className="testing-system"><div><strong>Задание</strong><div className="testing-indicators"><button className={`signal ${media.faceState}`} title={media.faceState === "green" ? "Лицо видно" : media.faceState === "yellow" ? "Лицо не видно или распознавание недоступно" : "Нет видео с камеры"} aria-label={`Камера: ${media.faceState}`}><MonitoringGlyph kind="face" /></button><button className={`signal ${media.screenState}`} onClick={() => void media.requestFullscreen()} title="Состояние полного экрана и фокуса за последние 30 секунд" aria-label={`Экран: ${media.screenState}`}><MonitoringGlyph kind="screen" /></button></div></div><b>{(question?.answered ?? 0) + 1}/&lt;36</b><p>Грейд по {subjects[subject]}</p><time>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</time><img className="testing-system-art" src={`${base}design/grade-card-fluff.png`} alt="" /><Action className="testing-danger" disabled={busy} onClick={() => setExitWarning(true)}>Закончить</Action></aside>
        {!screenLive && <div className="testing-capture-alert" role="alert"><p>Показ экрана остановлен. Подключите весь экран, чтобы продолжить тест.</p><Action onClick={() => void media.requestScreen()}>Подключить экран</Action></div>}
      </>}
      {phase === "finishing" && <section className="testing-finishing"><State loading={busy} /><h2>Сохраняем результаты</h2><p>Дождитесь отправки ответов и материалов прохождения.</p>{error && <Action onClick={() => sessionId && void run(() => complete(sessionId))}>Повторить сохранение</Action>}</section>}
      {(error || media.error || media.detectorError || media.uploadError) && <div className="testing-errors" role="alert">{[error, media.error, media.detectorError, media.uploadError].filter(Boolean).map((text, index) => <p key={index}>{text}</p>)}{media.uploadError && <Action onClick={() => void media.retryUploads().catch(() => {})}>Повторить отправку записи</Action>}</div>}
    </section>
    {phase === "confirm" && preflight && (preflight.eligible || preflight.active_session_id) && <Dialog title="Готовы пройти тест?" close={() => setPhase("setup")}><p>Тест по направлению «{subjects[subject]}». Меньше 36 вопросов: выбор ответа и ввод короткого ответа. Вам понадобятся полный экран и доступ к его показу. Камера — по желанию.</p><div className="testing-dialog-actions"><Action onClick={back}>Вернуться</Action><Action variant="primary" onClick={() => { void media.requestFullscreen(); setPhase("setup"); }}>Да, подготовиться</Action></div></Dialog>}
    {cameraWarning && <Dialog title="Продолжить без камеры?" close={() => setCameraWarning(false)}><p>Работодатель увидит, что вы отказались от видеоподтверждения честности тестирования.</p><div className="testing-dialog-actions"><Action variant="primary" onClick={() => { setCameraWarning(false); setWithoutCamera(false); void media.requestCamera(); }}>Включить камеру</Action><Action onClick={() => { setCameraWarning(false); setWithoutCamera(true); setCameraConsent(false); media.cameraStream?.getTracks().forEach(track => track.stop()); }}>Всё равно продолжить</Action></div></Dialog>}
    {rules && <Dialog title="Правила тестирования и согласие" close={() => setRules(false)}><p>Отвечайте самостоятельно, оставайтесь в полном экране и не переключайтесь на другие окна. Выход из полного экрана и потеря фокуса отмечаются в отчёте. Эти сигналы сами по себе не означают нарушение.</p><p>По вашему отдельному согласию Comit может записывать экран и видео с камеры для проверки честности. Материалы доступны только уполномоченным специалистам, хранятся 90 дней, затем удаляются. Работодатель видит отметку присутствия, а не видеозапись. Отказ от камеры не запрещает прохождение.</p><p>До нажатия «Начать» попытка не учитывается. Начатую попытку можно продолжить после обновления страницы; досрочное завершение не подтверждает грейд. Повторное прохождение доступно по сроку, который покажет платформа.</p><Action onClick={() => setRules(false)}>Понятно</Action></Dialog>}
    {exitWarning && <Dialog title="Завершить попытку без результата?" close={() => setExitWarning(false)}><p>Начатая попытка будет учтена. Грейд не будет подтверждён; срок следующего прохождения появится после завершения.</p><div className="testing-dialog-actions"><Action onClick={() => setExitWarning(false)}>Продолжить тест</Action><Action className="testing-danger" disabled={busy} onClick={() => sessionId && void run(async () => { setExitWarning(false); terminalIntent.current = "abandon"; await complete(sessionId); })}>Завершить</Action></div></Dialog>}
    {phase === "result" && result && <Dialog title={result.status === "abandoned" ? "Попытка завершена" : "Результат теста"} close={back}><div className="testing-result-grade">{result.decided_grade ? grades[result.decided_grade] : "Без подтверждения грейда"}</div><p>Направление: {subjects[subject]}. Ответов: {result.answered}.</p>{result.decided_grade && <p role="status">{result.grade_publication?.published ? "Подтверждённый результат сохранён в вашем резюме." : "Результат теста сохранён. Подтверждение в резюме ещё не завершено."}</p>}<p>Пройти ещё раз можно {retryDate(result.next_attempt_at)}.</p>{result.decided_grade && !result.grade_publication?.published && <Action disabled={busy} onClick={() => sessionId && void run(async () => { await api.finishTestingSession(sessionId); setResult(await api.testingResult(sessionId)); })}>Повторить подтверждение</Action>}<Action variant="primary" onClick={back}>Вернуться в резюме</Action></Dialog>}
  </main>;
}
