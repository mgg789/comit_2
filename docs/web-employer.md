# Кабинет работодателя (`apps/employer`)

> Статус (08.10): реализованы вход по email-коду, оболочка кабинета, список вакансий,
> создание/редактирование с публикацией. Остальные экраны макета — следующими итерациями.
> Макеты: Figma COMIT, секция «Работадатель» (node 771:1937).

## Запуск

```sh
npx -y pnpm@9.14.2 install                       # версия pnpm закреплена в package.json
npx -y pnpm@9.14.2 --filter @comit/web-employer dev   # http://localhost:5174/employer/
npx -y pnpm@9.14.2 --filter @comit/web-employer test  # vitest, чистая логика
```

Если глобальный pnpm новее (10+), он пытается скачать 9.14.2 по `packageManager` молча —
при медленной сети это выглядит как зависание; `npx pnpm@9.14.2` обходит это.

Dev-сервер проксирует `/api/*` на api-core (`API_PROXY_TARGET`, по умолчанию
`http://localhost:8080`) с отрезанием префикса — как nginx в `infra/nginx/nginx.conf`.
Для входа origin `http://localhost:5174` должен быть в `AUTH_ALLOWED_ORIGINS`.

## Устройство

| Путь | Что |
|---|---|
| `src/api.ts` | Единственный клиент `@comit/api-client`; сессия — HttpOnly cookie, токен в JS не хранится |
| `src/auth/` | `SessionProvider` (`GET /auth/employer/me`), экран входа, ввод одноразового кода |
| `src/layout/` | Оболочка: фон, верхняя навигация, меню аккаунта, заголовок страницы |
| `src/lib/use-resource.ts` | Загрузка данных экрана: loading/ready/error, отмена устаревших ответов, 401 → выход |
| `src/lib/format.ts` | Форматирование ЗП, статусов вакансии, подписей |
| `src/lib/explain.ts` | Перевод факторов объяснения matching-engine в текст; сам не считает скоринг |
| `src/vacancies/` | «Все вакансии», диалог создания/редактирования |

Маршруты (basename `/employer`): `/vacancies`, `/vacancies/new`.

## Вход

`POST /auth/employer/code` → экран кода → `POST /auth/employer/verify` с
`consent: {processing: true, version: "account-v1"}` (галочка согласия обязательна до
запроса кода, [согласие](legal/processing-consent.md)). Код — 6 цифр (api-core
`OTP_LENGTH`; в макете 5 ячеек — следуем контракту). Ввод принимает автозаполнение
`one-time-code` и вставку целиком. Ошибки `role_mismatch`, `invalid_code`,
`challenge_expired/exhausted`, `resend_cooldown`, лимиты — показываются по коду `detail`.
Кнопка «Яндекс» из макета не выводится: OAuth-входа в api-core нет.

## Вакансии

Список читает все страницы `GET /employer/vacancies` (keyset, до 10×50). Статус:
`business_status=open` → «Активная», `draft` → «Черновик», `closed` → «Закрыта»,
плюс состояние публикации (`creating`, `failed`). ЗП «от–до» обязательна и показывается
всегда (ТЗ §2.2).

Публикация — существующий контракт из трёх шагов: создать черновик (или CAS-патч
с `expected_revision`) → `approve` → `publish` с `summary_hash`. Сбой после сохранения
оставляет черновик. Форма заполняет поля, которые требует `_missing` в agents-service:
название, направление (`class`), ЗП от/до (вводятся в тыс. ₽, хранятся в ₽), город,
формат, график (`daily`); описание (`bio`) — необязательно.

## Дизайн-система

Токены и Tailwind-тема — `packages/ui-kit/src/tokens.css` и `theme.css` (импортируются
всеми четырьмя приложениями). Иконки из макета рендерятся CSS-маской и наследуют
`currentColor`. Шрифты самохостятся (офлайн-демо): Inter Variable и Unbounded 800 как
временная замена фирменных Murs Gothic / Moon Walk — файлы шрифтов нужны от дизайнера.

## Недостающие контракты

| Что в макете | Нужно от backend | Где ждёт фронт |
|---|---|---|
| «N дней назад» на карточке вакансии | `created_at`/`updated_at` в `AgentsDraftView` | `VacanciesPage` → `VacancyCard` |
| Статус «Приостановлена» | бизнес-статус паузы вакансии | `lib/format.ts` `vacancyBadge` |
| Поле «Грейд» вакансии | поле грейда в `AgentsDraftFields` и в matching intake | `VacancyFormDialog` |
| Выбор «Стек» | каталог координат strict-schema для работодателя (`GET /employer/taxonomy`; сейчас есть только `/candidate/taxonomy`) | `VacancyFormDialog` |
| Вход через Яндекс | OAuth-провайдер в api-core | `LoginPage` |
