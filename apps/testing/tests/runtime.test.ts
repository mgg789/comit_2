import { ApiError } from "@comit/api-client";
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { answerReady, publicationMessage, readSubject, retryDate, testingError } from '../src/runtime';
test('untrusted subject deep link cannot choose unsupported test', () => { assert.equal(readSubject('javascript:alert(1)'), 'algorithms'); assert.equal(readSubject('react'), 'react'); });
test('choice zero is a real answer; out-of-range, blanks and wrong answer types are blocked', () => { const task = {item_id:'a',family:'sql',level:2,type:'mcq' as const,prompt:'test',options:['a','b']}; assert.ok(answerReady(task,0)); assert.equal(answerReady(task,2),false); assert.equal(answerReady(task,'0'),false); assert.equal(answerReady({...task,type:'extended'},'  '),false); assert.ok(answerReady({...task,type:'code'},'def f(): pass')); });
test('invalid cooldown cannot render an invalid date',()=> assert.equal(retryDate('bad'), 'после обновления данных'));

test("judge outage keeps the answer retryable without suggesting a failed grade", () => {
  assert.match(testingError(new ApiError(503, "/testing/answer", {})), /ответ не засчитан/);
});

test("explicit disabled mode explains that retrying cannot start a test", () => {
  assert.match(testingError(new ApiError(503, "/testing/sessions", { detail: "testing_disabled" })), /Тестирование отключено/);
});

test("publication cooldown explains a terminal rejection instead of pretending the API is unreachable", () => {
  assert.match(publicationMessage({ published: false, status: "api_status:409", retryable: false, detail: "grade_upgrade_cooldown", retry_at: "2030-02-01T12:00:00Z" }), /Повышение грейда доступно/);
  assert.match(publicationMessage({ published: false, status: "api_unreachable", retryable: false }), /повторная отправка этой попытки отключена/);
});

test("uncertain rubric is explained as unjudged instead of incorrect", () => {
  assert.match(testingError(new ApiError(503, "/testing/answer", { detail: "rubric:uncertain" })), /не смогла уверенно оценить/);
});
