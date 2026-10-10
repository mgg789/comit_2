import type { CandidateTestingHistory } from "@comit/shared";
import { testingActivityCalendar, testingActivityIntensity } from "./testing-activity";

function calendarLabel(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
}

/** Render only server-confirmed test results, grouped by Moscow date; unknown source data is never zero-filled. */
export function TestingHeatmap({ history }: { history: CandidateTestingHistory }) {
  const cells = testingActivityCalendar(history.daily);
  return <div className="candidate-testing-activity">
    <div className="candidate-testing-summary"><span>Подтверждённые тесты · последние 30 дней</span><strong>{history.total}</strong></div>
    <div className="candidate-testing-calendar"><div className="candidate-testing-weekdays" aria-hidden="true">{["Пн", "", "Ср", "", "Пт", "", "Вс"].map((label, index) => <span key={index}>{label}</span>)}</div><div className="candidate-testing-days" role="list" aria-label="Результаты тестов по дням">{cells.map((day, index) => day ? <span role="listitem" tabIndex={0} key={day.date} className={`candidate-testing-cell intensity-${testingActivityIntensity(day.count)}`} aria-label={`${calendarLabel(day.date)}: подтверждённых тестов ${day.count}`}><span className="candidate-testing-tooltip" role="tooltip">{calendarLabel(day.date)}: {day.count}</span></span> : <span key={`blank-${index}`} className="candidate-testing-cell is-outside" aria-hidden="true" />)}</div></div>
    <div className="candidate-testing-calendar-range"><span>{calendarLabel(history.period.from)}</span><span>{calendarLabel(history.period.to)}</span></div>
    <div className="candidate-testing-legend"><span>Меньше</span>{[0, 1, 2, 3, 4].map(level => <span key={level} className={`candidate-testing-cell intensity-${level}`} aria-hidden="true" />)}<span>Больше</span></div>
    {!history.total && <p className="muted">За последние 30 дней подтверждённых тестов пока нет.</p>}
  </div>;
}
