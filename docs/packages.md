# Пакеты, компоненты и лицензии

Исходный код Comit — [Apache-2.0](../LICENSE). [NOTICE](../NOTICE) сохраняет границу с лицензиями сторонних компонентов; это не перелицензирование шрифтов, моделей или чужих изображений.

## Структура

- `apps/{candidate,employer,testing,welcome,support}` — React/TypeScript приложения, владелец Raipus.
- `apps/api` — Fastify API; `packages/shared` — общие контракты; `packages/api-client` — клиент API; владелец mgg789.
- `packages/ui-kit` — общие визуальные компоненты и токены, владелец Raipus.
- `services/*` — Python engines, владелец mgg789.

Все npm workspace-пакеты приватные: установка и сборка локальные, публикация в npm/GitHub Packages не настроена. Это предотвращает случайную публикацию внутренних контрактов. Node >=22, pnpm 9.14.2; точные разрешённые версии JS-зависимостей зафиксированы в [pnpm-lock.yaml](../pnpm-lock.yaml). Python требует >=3.12; диапазоны зависимостей — `services/*/pyproject.toml`, для matching есть `uv.lock`. Для остальных Python engines следующий шаг — собственный полный lockfile; пока их транзитивные версии задаются диапазонами `pyproject.toml`.

## Сторонние ресурсы

- MediaPipe tasks vision 0.10.32, WASM и face detector: `apps/testing/public/mediapipe/` (лицензия — `LICENSE` рядом с файлами) и модель `apps/testing/public/models/` (см. её `README.md`). Apache-2.0.
- Matching PDF-шрифты: `services/matching-engine/src/matching_engine/fonts/LICENSE.txt`.
- Дизайн и графические ресурсы Figma сохраняют авторство дизайнеров; Apache-2.0 относится к коду интеграции, не автоматически ко всем ресурсам.
- PDF/OCR использует pypdf, Pillow, Tesseract и Poppler. Они запускаются локально; их собственные лицензии и зависимости входят в образы согласно пакетному менеджеру ОС.

Проверки: `pnpm install --frozen-lockfile`, `pnpm -r typecheck`, `pnpm -r build`, Python suites из [ci.md](ci.md). Для точного инвентаря конкретного образа используйте `python -m pip freeze` внутри него и `pnpm list -r --depth 0` при сборке. Не публикуйте вместе с инвентарём environment или секреты.
