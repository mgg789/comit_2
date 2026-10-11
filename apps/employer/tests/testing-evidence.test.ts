import test from 'node:test';
import assert from 'node:assert/strict';
import { testingPresenceLabel, testingSubjectLabel } from '../src/testing-evidence';
const result = {subject:'sql',grade:'middle' as const,supervision_badge:'не определён'};
test('absence of camera and detector failure remain distinct to the employer',()=> {
 assert.match(testingPresenceLabel({...result,camera_status:'denied'}),/без камеры/);
 assert.match(testingPresenceLabel({...result,camera_status:'unavailable'}),/техническим/);
 assert.match(testingPresenceLabel({...result,camera_status:'unknown'}),/Данных/);
 assert.equal(testingSubjectLabel('sql'),'SQL');
});
