import "./weekly-challenge.css";
import { useState } from "react";
import type { WeeklyChallengeView } from "@comit/shared";
import { Dialog } from "@comit/ui-kit/app-shell";
import { Action, State } from "@comit/ui-kit/design";
import { api, errorText, useResource } from "./runtime";
const read = () => api.get<WeeklyChallengeView>("/candidate/weekly-challenge");
/** Weekly practice stays separate from grade confirmation and persists across reloads. */
export function WeeklyChallenge({ changed }: { changed: () => void }) {
 const resource=useResource(read), [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null),[answers,setAnswers]=useState<Record<string,string>>({});
 const task=resource.data;
 async function start(){setBusy(true);setError(null);try{resource.setData(await api.post<WeeklyChallengeView>("/candidate/weekly-challenge/start",{}));setOpen(true);}catch(e){setError(errorText(e));}finally{setBusy(false);}}
 async function submit(){if(!task)return;setBusy(true);setError(null);try{const result=await api.post<WeeklyChallengeView>("/candidate/weekly-challenge/submit",{week:task.week,answers});resource.setData(result);changed();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
 const expiry=task?new Date(task.expires_at).toLocaleString("ru-RU",{day:"numeric",month:"long",hour:"2-digit",minute:"2-digit"}):"";
 return <section className="panel candidate-weekly-task"><div><h3>Задание недели</h3><p className="muted">Три коротких вопроса на работу с данными и логику. Два верных ответа дают отметку активности до начала следующей недели. Она учитывается только среди близких по соответствию кандидатов и не меняет грейд.</p>{task?.status==="completed"&&<p role="status">{task.passed?"Задание выполнено. Отметка активности получена.":"В этот раз не получилось. Новое задание появится на следующей неделе."} Верных ответов: {task.correct} из 3.</p>}{task&&<small>Новое задание: {expiry}. Одна попытка в неделю.</small>}<State loading={resource.loading} error={resource.error} retry={resource.refresh}/>{error&&<p role="alert">{error}</p>}</div><Action variant="light" disabled={busy||resource.loading||!task} onClick={()=>task?.status==="completed"?setOpen(true):void start()}>{task?.status==="completed"?"Результат":task?.status==="started"?"Продолжить":"Пройти задание"}</Action>
 {open&&task&&<Dialog size="wide" title="Задание недели" close={()=>{if(!busy)setOpen(false);}}>{task.status==="completed"?<><p>Верных ответов: {task.correct} из 3.</p><p>{task.passed?`Отметка активности действует до ${expiry}.`:"Попробуйте новое задание на следующей неделе."}</p><Action onClick={()=>setOpen(false)}>Готово</Action></>:<form onSubmit={e=>{e.preventDefault();void submit();}}>{task.questions.map(q=><fieldset key={q.id}><legend>{q.prompt}</legend>{q.choices.map(choice=><label className="weekly-choice" key={choice}><input type="radio" name={q.id} value={choice} checked={answers[q.id]===choice} onChange={()=>setAnswers(prev=>({...prev,[q.id]:choice}))}/><span>{choice}</span></label>)}</fieldset>)}{error&&<p role="alert">{error}</p>}<Action type="submit" variant="primary" disabled={busy||task.questions.some(q=>!answers[q.id])}>{busy?"Проверяем…":"Отправить ответы"}</Action></form>}</Dialog>}
 </section>;
}
