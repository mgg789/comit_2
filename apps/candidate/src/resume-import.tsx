import { useRef, useState } from "react";
import type { ResumeImportRequest, ResumeImportResult } from "@comit/shared";
import { Action, Checkbox, Field } from "@comit/ui-kit/design";
import { api } from "./runtime";

/** Optional first editor step: private extraction, visible review and explicit draft-only application. */
export function ResumeImport({ onApply }: { onApply: (value: ResumeImportResult["fields"]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [consent, setConsent] = useState(false), [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ResumeImportResult | null>(null), [error, setError] = useState<string | null>(null);
  async function upload(file: File | undefined) {
    if (!file || !consent || busy) return;
    setError(null); setResult(null);
    if (file.size > 5 * 1024 * 1024 || !/\.(pdf|png|jpe?g|webp)$/i.test(file.name)) { setError("Выберите PDF, PNG, JPG или WebP размером до 5 МБ."); return; }
    setBusy(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
      const body: ResumeImportRequest = { filename: file.name, content_base64: btoa(binary), processing_consent: true };
      setResult(await api.post<ResumeImportResult>("/candidate/profile/import", body));
    } catch {
      setError("Не удалось распознать резюме. Попробуйте чёткое фото или PDF без пароля (до 10 страниц). Можно заполнить форму вручную.");
    } finally { setBusy(false); if (input.current) input.current.value = ""; }
  }
  return <section className="panel candidate-editor-group" aria-labelledby="resume-import-title"><h2 id="resume-import-title">Начните с готового резюме</h2>
    <p>Загрузите PDF или фотографию, чтобы заполнить форму. Перед сохранением вы сможете всё проверить и исправить.</p>
    <label className="candidate-consent-check"><Checkbox checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />Разрешаю обработать файл для заполнения резюме. Исходный файл удаляется после распознавания.</label>
    <div className={`resume-dropzone ${dragging ? "is-dragging" : ""} ${!consent || busy ? "is-disabled" : ""}`} onDragOver={event => { event.preventDefault(); if (consent && !busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length > 1) { setError("Загрузите один файл резюме."); return; } void upload(event.dataTransfer.files[0]); }}>
      <button type="button" disabled={!consent || busy} onClick={() => input.current?.click()} aria-label="Загрузить PDF или фотографию резюме"><strong>{busy ? "Распознаём резюме…" : "Перетащите резюме сюда"}</strong><span>{consent ? "или нажмите, чтобы выбрать файл" : "Сначала разрешите обработку файла выше"}</span><small>До 5 МБ и 10 страниц · PDF, PNG, JPG, WebP</small></button>
      <input ref={input} className="resume-file-input" type="file" aria-label="Файл резюме" accept="application/pdf,image/png,image/jpeg,image/webp" disabled={!consent || busy} onChange={event => void upload(event.target.files?.[0])} />
    </div>
    {busy && <p role="status">Распознаём резюме… Это может занять до минуты.</p>}{error && <p className="form-error" role="alert">{error}</p>}
    {result && <div><h3>Проверьте распознанные данные</h3>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}
      <p>{[result.fields.display_name, result.fields.city, result.fields.phone, result.fields.primary_direction, result.fields.declared_grade].filter(Boolean).join(" · ")}</p>
      <details><summary>Полный распознанный текст</summary><Field label="Текст исходного резюме"><textarea readOnly rows={10} value={result.extracted_text} /></Field></details>
      <p>Заполним только пустые поля. Уже введённые данные сохранятся. Затем проверьте форму ниже и нажмите «Сохранить резюме».</p>
      <Action type="button" variant="primary" onClick={() => { onApply(result.fields); setResult(null); }}>Заполнить пустые поля</Action>
    </div>}
  </section>;
}
