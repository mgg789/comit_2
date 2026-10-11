import { useState, type FormEvent } from "react";
import { usePersistentState, useClearPersistentState } from "@comit/ui-kit/ui-state";
import type { AccountView } from "@comit/shared";
import { api, errorText, useResource } from "./runtime";
import { useEmployerPreferences } from "./preferences";
import { Checkbox } from "@comit/ui-kit/design";
import { Action, Field, PageTitle, Select, State } from "./ui";
import "./settings.css";

/** Authenticated account and parent-owned confirmation actions for sensitive operations. */
export interface EmployerSettingsProps {
  account: AccountView;
  onConfirmLogoutAll: () => void;
  onConfirmDelete: () => void;
}

/** Micro-USD integers retain their precision and unit; malformed values are never invented. */
function money(value: string): string {
  if (!/^\d+$/.test(value)) return "Данные недоступны";
  const amount = BigInt(value);
  const whole = (amount / 1_000_000n).toLocaleString("ru-RU");
  const fraction = (amount % 1_000_000n).toString().padStart(6, "0").replace(/0+$/, "");
  return `${whole}${fraction ? `,${fraction}` : ""} USD`;
}

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "Дата недоступна"
    : parsed.toLocaleDateString("ru-RU");
}

/** Display real account/quota data and browser-local controls without implying remote settings writes. */
export function Settings({
  account,
  onConfirmLogoutAll,
  onConfirmDelete,
}: EmployerSettingsProps) {
  const organization = useResource(() => api.getEmployerProfile());
  const [companyDraft, setCompanyDraft] = usePersistentState("organization.company.draft", { value: "", dirty: false });
  const clearDraft = useClearPersistentState();
  const [savingCompany, setSavingCompany] = useState(false), [companyError, setCompanyError] = useState<string | null>(null), [companySaved, setCompanySaved] = useState(false);
  const company = companyDraft.dirty ? companyDraft.value : organization.data?.company_name || "";
  async function saveCompany(event: FormEvent) {
    event.preventDefault();
    if (!organization.data || savingCompany) return;
    if (!company.trim()) { setCompanyError("Укажите название компании."); return; }
    setSavingCompany(true); setCompanyError(null); setCompanySaved(false);
    try { const updated = await api.updateEmployerProfile({ expected_revision: organization.data.revision, company_name: company.trim() }); organization.setData(updated); clearDraft("organization.company.draft"); setCompanyDraft({ value: "", dirty: false }); setCompanySaved(true); }
    catch (reason) { setCompanyError(errorText(reason)); }
    finally { setSavingCompany(false); }
  }
  const budget = useResource(() => api.getOrganizationBudget());
  const { preferences, updatePreferences } = useEmployerPreferences();
  return (
    <section className="settings-screen">
      <PageTitle title="Настройки" />
      <div className="settings-grid">
        <section className="settings-card glass" aria-labelledby="settings-account">
          <h2 id="settings-account">Аккаунт</h2>
          <p className="settings-description">Данные вашего аккаунта работодателя</p>
          <dl className="settings-facts">
            <div><dt>Email</dt><dd>{account.email}</dd></div>
            <div><dt>Статус email</dt><dd>{account.email_verified ? "Подтверждён" : "Не подтверждён"}</dd></div>
            <div><dt>Дата регистрации</dt><dd>{date(account.created_at)}</dd></div>
            <div><dt>Согласие на обработку данных</dt><dd>{date(account.consent.accepted_at)} · версия {account.consent.version}</dd></div>
          </dl>
        </section>
        <section className="settings-card glass" aria-labelledby="settings-company"><h2 id="settings-company">Компания</h2><p className="settings-description">Это название автоматически подставляется во все новые приглашения. Ранее отправленные приглашения сохраняют прежнее название.</p>{organization.loading || organization.error ? <State loading={organization.loading} error={organization.error} retry={organization.refresh} /> : <form className="settings-company-form" onSubmit={saveCompany}><Field label="Название компании"><input required maxLength={200} value={company} disabled={savingCompany} onChange={event => { setCompanyDraft({ value: event.target.value, dirty: true }); setCompanySaved(false); }} /></Field><Action type="submit" variant="primary" disabled={savingCompany}>{savingCompany ? "Сохраняем…" : "Сохранить название"}</Action>{companyError && <p className="form-error" role="alert">{companyError}</p>}{companySaved && <p role="status">Название компании сохранено.</p>}</form>}</section>
        <section className="settings-card glass" aria-labelledby="settings-ai">
          <h2 id="settings-ai">AI-ассистент</h2>
          <p className="settings-description">Настройки ввода и глубина рассуждений при открытии AI в этом браузере.</p>
          <Field label="Глубина рассуждений">
            <Select aria-label="Глубина рассуждений по умолчанию" value={preferences.reasoningEffort} onChange={(event) => {
              const value = event.target.value;
              if (value === "off" || value === "medium" || value === "max") updatePreferences({ reasoningEffort: value });
            }}>
              <option value="off">Выключены</option>
              <option value="medium">Средняя</option>
              <option value="max">Максимальная</option>
            </Select>
          </Field>
          <label className="settings-toggle">
            <span><strong>Отправлять по Enter</strong><small>{preferences.submitWithEnter ? "Shift + Enter — новая строка" : "Enter — новая строка; отправка кнопкой"}</small></span>
            <Checkbox role="switch" checked={preferences.submitWithEnter} onChange={(event) => updatePreferences({ submitWithEnter: event.target.checked })} />
          </label>
          <label className="settings-toggle">
            <span><strong>Уменьшить анимацию</strong><small>Меньше движения в интерфейсе</small></span>
            <Checkbox role="switch" checked={preferences.reducedMotion} onChange={(event) => updatePreferences({ reducedMotion: event.target.checked })} />
          </label>
          <p className="settings-description settings-local">Настройки сохраняются только в этом браузере. Если браузер запрещает хранение, они действуют до перезагрузки страницы.</p>
        </section>
        <section className="settings-card glass" aria-labelledby="settings-budget">
          <div className="settings-card-heading"><h2 id="settings-budget">Бюджет AI</h2><Action onClick={budget.refresh} disabled={budget.loading}>Обновить</Action></div>
          <p className="settings-description">Текущие лимиты организации по данным сервера</p>
          {budget.data ? <>
            <dl className="settings-facts">
              <div><dt>Лимит</dt><dd>{money(budget.data.limit_micro_usd)}</dd></div>
              <div><dt>Использовано</dt><dd>{money(budget.data.spent_micro_usd)}</dd></div>
              <div><dt>Зарезервировано</dt><dd>{money(budget.data.reserved_micro_usd)}</dd></div>
              <div><dt>Доступно</dt><dd>{money(budget.data.available_micro_usd)}</dd></div>
              <div><dt>Период учёта</dt><dd>{budget.data.window_days != null ? `${budget.data.window_days} дней` : "Сервер не указал период"}</dd></div>
            </dl>
            {!budget.data.pricing_configured && <p className="settings-description">Стоимость AI-запросов на сервере ещё не настроена.</p>}
          </> : <State loading={budget.loading} error={budget.error} retry={budget.error ? budget.refresh : undefined} />}
        </section>
        <section className="settings-card glass" aria-labelledby="settings-security">
          <h2 id="settings-security">Безопасность</h2>
          <div className="settings-security-row"><div><strong>Завершить все сессии</strong><p className="settings-description">Выход из аккаунта работодателя на всех устройствах.</p></div><Action onClick={onConfirmLogoutAll}>Выйти везде</Action></div>
          <div className="settings-security-row"><div><strong>Удаление аккаунта</strong><p className="settings-description">Удаление пока недоступно. Откройте информацию о текущих ограничениях.</p></div><Action className="settings-danger" onClick={onConfirmDelete}>Об удалении</Action></div>
        </section>
      </div>
    </section>
  );
}
