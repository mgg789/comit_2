import { useState } from "react";
import type { CandidateVerificationDocument, CandidateVerificationDocumentUpload, CandidateProfileFields } from "@comit/shared";
import { Action, Field } from "@comit/ui-kit/design";
import { api, errorText, useResource } from "./runtime";

/** Validate and upload an achievement declaration through the owner-scoped encrypted store. */
export async function uploadAchievementFile(file: File, claimId: string): Promise<void> {
  if (file.size === 0 || file.size > 10 * 1024 * 1024 || !["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("Выберите PDF или фотографию до 10 МБ.");
  const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  const body: CandidateVerificationDocumentUpload = { filename: file.name, content_type: file.type, content_base64: btoa(binary), claim_kind: "achievements", claim_id: claimId };
  await api.post("/candidate/profile/verification-documents", body);
}

/** File declarations stay private and pending until an independent verification. */
export function AchievementDocuments({ claimId, editable = false }: { claimId: string; editable?: boolean }) {
  const resource = useResource(() => api.get<CandidateVerificationDocument[]>("/candidate/profile/verification-documents"), [claimId]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const files = resource.data?.filter(file => file.claim_kind === "achievements" && file.claim_id === claimId) ?? [];
  async function upload(file: File | undefined) {
    if (!file || busy) return;
    if (file.size === 0 || file.size > 10 * 1024 * 1024 || !["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("Выберите PDF или фотографию до 10 МБ."); return; }
    setError(null); setBusy(true);
    try {
      await uploadAchievementFile(file, claimId); resource.refresh();
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  async function download(file: CandidateVerificationDocument) {
    setError(null);
    try {
      const response = await fetch(`/api/candidate/profile/verification-documents/${encodeURIComponent(file.id)}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error("download_failed");
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
      link.href = url; link.download = file.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch { setError("Не удалось скачать документ. Попробуйте ещё раз."); }
  }
  return <div className="candidate-achievement-files">{files.map(file => <Action key={file.id} type="button" onClick={() => void download(file)}>{file.filename} · скачать</Action>)}
    {editable && <Field label="Фото или PDF достижения" hint="До 10 МБ. Файл хранится отдельно от ссылки и ожидает проверки."><input type="file" accept="application/pdf,image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ""; }} /></Field>}
    {busy && <p role="status">Загружаем документ…</p>}{resource.error && <Action type="button" onClick={resource.refresh}>Повторить загрузку документов</Action>}{error && <p role="alert">{error}</p>}
  </div>;
}

/** Keep server-owned narrative IDs; temporary editor IDs never cross the profile API boundary. */
export function profileFieldsForSave(fields: CandidateProfileFields, saved: CandidateProfileFields | undefined): CandidateProfileFields {
  const result = { ...fields };
  for (const group of ["experience", "projects", "achievements"] as const) {
    if (!fields[group]) continue;
    const known = new Set((saved?.[group] ?? []).map(item => item.id));
    result[group] = fields[group]?.map(item => {
      if (item.id && known.has(item.id)) return item;
      const { id: _temporaryId, ...declaration } = item;
      return declaration;
    });
  }
  return result;
}
