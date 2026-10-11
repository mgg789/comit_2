# Архитектура в диаграммах

Диаграммы Mermaid по фактической конфигурации `infra/docker-compose.yml`,
`infra/nginx/nginx.conf` и `.github/workflows/*`. Текстовое описание контуров —
[architecture.md](architecture.md), карточки сервисов — [services.md](services.md).

## Контейнеры и связи

```mermaid
flowchart LR
  subgraph Browser["Браузер"]
    SPA["SPA: welcome, candidate,<br/>employer, testing, support"]
  end
  subgraph Host["Сервер (compose-проект comit)"]
    WEB["web / nginx :80"]
    API["api-core :8080"]
    ME["matching-engine :8101<br/>+ 6 workers"]
    TE["testing-engine :8102"]
    IE["integration-engine :8103"]
    AS["agents-service :8104<br/>+ agents-worker, organization-agent-worker"]
    VE["verification-engine :8105"]
    AW["auth-mail-worker,<br/>candidate-profile-worker,<br/>recommendation-worker"]
    PG[("PostgreSQL 16<br/>+ pgvector")]
    subgraph CP["сеть chat-private"]
      QG["qgramm-chat"]
    end
  end
  subgraph JudgeVM["Отдельная VM"]
    CJ["code-judge :8106<br/>Docker на каждый testcase"]
  end
  EXT["DeepSeek, Jev, SMTP, hh.ru,<br/>публичные Git-хостинги"]

  SPA -->|HTTPS| WEB
  WEB -->|"/api/* (кроме /api/internal)"| API
  API --> ME
  API --> TE
  API --> AS
  API --> VE
  API --> QG
  AW --> ME
  AW --> AS
  AS --> ME
  AS -->|"инструменты агента"| API
  TE -->|"session-result"| API
  TE --> CJ
  VE --> IE
  API --> PG
  ME --> PG
  AS --> PG
  AW --> PG
  AS -.-> EXT
  TE -.-> EXT
  AW -.-> EXT
  VE -.-> EXT
```

Наружу смотрит только web; служебные порты публикуются на `127.0.0.1` для smoke и
диагностики. Межсервисные вызовы защищены токенами (`CANDIDATE_PIPELINE_TOKEN`,
`AGENTS_SERVICE_TOKEN`, `TESTING_ENGINE_TOKEN`, `VERIFICATION_ENGINE_TOKEN`,
`CHAT_SERVICE_TOKEN`, `CODE_JUDGE_TOKEN`).

## Порядок старта

```mermaid
flowchart LR
  PG[(postgres)] --> CM[candidate-migrate] --> AM[agents-migrate] --> UM[auth-migrate]
  UM --> SVC["HTTP-сервисы и workers"] --> WEB[web]
```

## Конвейер подбора

```mermaid
flowchart TD
  P["Профиль кандидата<br/>(api-core)"] -->|outbox| PW[candidate-profile-worker]
  PW -->|"PUT /internal/candidates/{id}/profile"| CW["candidate worker:<br/>векторы, PDF"]
  CW --> POOL[("Пул кандидатов")]
  V["Вакансия<br/>(agents-service)"] -->|agents-worker publish| VW["vacancy worker → ready"]
  VW --> CB["combine worker:<br/>строгий фильтр → гибридный скоринг → main top-k"]
  POOL --> CB
  CB --> RR["rerank worker:<br/>sort-only R₀ или main_fallback"]
  RR --> SEL["Подборки recommended / reserve"]
  SEL --> FEED["Лента работодателя"]
  FEED -->|"лайк / дизлайк"| PREF["preference-worker:<br/>vacancy-local поправка"]
  PREF --> FEED
  FEED --> REF["feed-refill-worker"]
  REF --> FEED
  FEED -.->|"research-показы"| HYP["Гипотезы"]
```

Подробности этапов — [matching.md](matching.md), [preference-learning.md](preference-learning.md),
[hypothesis-learning.md](hypothesis-learning.md), [feed-refill.md](feed-refill.md).

## Адаптивный тест

```mermaid
sequenceDiagram
  participant B as Браузер /testing/
  participant A as api-core
  participant T as testing-engine
  participant J as code-judge
  participant L as Jev / DeepSeek
  B->>A: /candidate/testing/sessions…
  A->>T: /testing/sessions… (X-Internal-Token, X-Candidate-ID)
  T->>J: код → POST /submissions
  T->>L: развёрнутый ответ → рубрика
  T-->>A: next / result
  T->>A: POST /internal/testing/session-result
  A->>A: grade:trusted evidence, ревизия профиля, outbox
```

Таймауты прокси api-core: 65 с для `answer`, 25 с для остальных вызовов, тело до
3.1 MB ([testing-engine.md](testing-engine.md)).

## CI/CD

```mermaid
flowchart LR
  PR["pull_request, push в dev/main"] --> CI["ci.yml: js, python, code-judge-isolation,<br/>regression, api, smoke"]
  MAIN["push в main"] --> DEP["deploy.yml: deploy (self-hosted)"] --> PS["public-smoke"]
```

Состав job-ов, раннеры и пороги покрытия — [ci.md](ci.md), выкладка — [deploy.md](deploy.md).
