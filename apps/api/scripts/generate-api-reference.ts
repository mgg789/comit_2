/** Generate the complete route inventory from configured OpenAPI; never connects to real accounts. */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createApiServer } from '../src/server.js';
Object.assign(process.env, { AUTH_SECRET: 'synthetic-documentation-only-configuration-12345', AUTH_ALLOWED_ORIGINS: 'http://localhost:8088', AUTH_COOKIE_SECURE: 'false', NODE_ENV: 'development', SUPPORT_EMAIL: 'support@comit-demo.invalid', SUPPORT_ACCESS_KEY: 'synthetic-docs-operator-only-1234567890', DATABASE_URL: 'postgres://comit:synthetic@127.0.0.1:1/docs_only' });
const app = await createApiServer();
try {
  await app.ready();
  const schema = app.swagger();
  const groups = new Map<string, string[]>();
  let total = 0;
  for (const [path, item] of Object.entries(schema.paths ?? {}).sort()) {
    for (const [method, operation] of Object.entries(item ?? {})) {
      if (!['get','post','put','patch','delete','head','options'].includes(method) || !operation) continue;
      const tag = operation.tags?.[0] ?? 'untagged';
      const access = (operation.security ?? []).flatMap((entry: Record<string, unknown>) => Object.keys(entry)).join(', ') || 'без сессии';
      const row = `| ${method.toUpperCase()} | \`${path}\` | ${access} |`;
      groups.set(tag, [...(groups.get(tag) ?? []), row]); total += 1;
    }
  }
  let text = '# Справочник маршрутов api-core\n\n';
  text += `Полный перечень из OpenAPI текущего api-core: **${total} операций** при настроенной поддержке. Браузер добавляет префикс \`/api\`; внешние \`/api/internal/*\` и \`/api/metrics\` закрыты gateway. Подробности: [api.md](api.md).\n\n`;
  text += '## Группы\n\n| OpenAPI tag | Операций |\n|---|---|\n';
  for (const [tag, rows] of groups) text += `| ${tag} | ${rows.length} |\n`;
  for (const [tag, rows] of groups) text += `\n## ${tag}\n\n| Метод | Путь | Доступ |\n|---|---|---|\n${rows.join('\n')}\n`;
  text += '\n## Обновление\n\n`pnpm --filter @comit/api exec tsx scripts/generate-api-reference.ts` обновляет документ из зарегистрированных схем. С `--check` команда проверяет точное совпадение и число операций. Генератор использует синтетическую конфигурацию, не запускает HTTP-сервер и не читает production env.\n';
  const target = fileURLToPath(new URL('../../../docs/api-reference.md', import.meta.url));
  if (process.argv.includes('--check')) { if (readFileSync(target, 'utf8') !== text) throw new Error('api_reference_stale'); }
  else writeFileSync(target, text);
  console.log(`API reference: ${total} operations`);
} finally { await app.close(); }
