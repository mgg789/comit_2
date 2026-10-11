import test from 'node:test';
import assert from 'node:assert/strict';
import { faceIndicator, screenIndicator, cameraObservation, ProctoringCounters, MediaUploadQueue } from '../src/proctoring.ts';
test('focus warning has priority and expires exactly after 30 seconds', () => {
  assert.equal(screenIndicator(40_000, true, true, 20_000, 15_000), 'red');
  assert.equal(screenIndicator(45_000, true, true, 20_000, 15_000), 'yellow');
  assert.equal(screenIndicator(50_000, true, true, 20_000, 15_000), 'green');
  assert.equal(faceIndicator(true, 0), 'yellow'); assert.equal(faceIndicator(false, 1), 'red');
});
test('counters sum intervals rather than time since the last frame', () => {
  const c = new ProctoringCounters(0);
  c.observe(0, 1, true, 'granted'); c.observe(1000, 0, false, 'granted');
  c.observe(2000, 2, true, 'granted'); c.observe(3000, 2, true, 'granted');
  assert.deepEqual(c.snapshot(), { total_seconds: 3, in_frame_seconds: 2, focus_lost_seconds: 1, multi_face_events: 1, camera: 'granted' });
});
test('failed chunks retain sequence and are retried in order', async () => {
  const calls: number[] = []; let failed = false;
  const q = new MediaUploadQueue(async (_, __, n) => { calls.push(n); if (!failed) { failed = true; throw new Error('offline'); } });
  q.add('screen', new Blob(['one'])); q.add('webcam', new Blob(['two']));
  await assert.rejects(q.drain()); await q.drain(); assert.deepEqual(calls, [0, 0, 1]);
});
test('media queue signals backpressure without discarding the crossing chunk', async () => {
  let uploaded = '';
  const q = new MediaUploadQueue(async (_, blob) => { uploaded += await blob.text(); }, 2);
  assert.throws(() => q.add('screen', new Blob(['123']))); await q.drain();
  assert.equal(uploaded, '123');
});

test('large background-delayed recorder chunks split before upload without changing retry identities', async () => {
  const sizes: number[] = []; const sequences: number[] = [];
  const q = new MediaUploadQueue(async (_, blob, n) => { sizes.push(blob.size); sequences.push(n); });
  q.add('screen', new Blob([new Uint8Array(5 * 1024 * 1024)])); await q.drain();
  assert.deepEqual(sizes, [2 * 1024 * 1024, 2 * 1024 * 1024, 1024 * 1024]);
  assert.deepEqual(sequences, [0, 1, 2]);
});

test('resume continues sequence after server-acknowledged chunks', async () => {
  const sequences: number[] = [];
  const q = new MediaUploadQueue(async (_, __, n) => { sequences.push(n); }, undefined, 19);
  q.add('screen', new Blob(['new screen'])); q.add('webcam', new Blob(['new camera'])); await q.drain();
  assert.deepEqual(sequences, [19, 20]);
  assert.throws(() => new MediaUploadQueue(async () => {}, undefined, -1));
});

test('resumed supervision preserves earlier lost focus and face absence', () => {
  const c = new ProctoringCounters(10_000, {total_seconds: 20, in_frame_seconds: 5, focus_lost_seconds: 7, multi_face_events: 2, camera: 'granted'});
  c.observe(10_000, 1, true, 'granted'); c.observe(12_000, 1, true, 'granted');
  assert.deepEqual(c.snapshot(), {total_seconds: 22, in_frame_seconds: 7, focus_lost_seconds: 7, multi_face_events: 2, camera: 'granted'});
});
test('resume cannot hide an unavailable or declined camera in earlier observations', () => {
  const c = new ProctoringCounters(0, {total_seconds: 20, in_frame_seconds: 0, focus_lost_seconds: 0, multi_face_events: 0, camera: 'unavailable'});
  c.observe(0, 1, true, 'granted'); c.observe(1000, 1, true, 'granted');
  assert.equal(c.snapshot().camera, 'unavailable');
  assert.throws(() => new ProctoringCounters(0, {total_seconds: 1, in_frame_seconds: 2, focus_lost_seconds: 0, multi_face_events: 0, camera: 'granted'}));
});

test('slow model startup followed by success never latches a technical camera failure', () => {
  const c = new ProctoringCounters(0);
  c.observe(0, 0, true, cameraObservation(true, 'loading'));
  c.observe(10_000, 0, true, cameraObservation(true, 'loading'));
  assert.equal(c.snapshot().camera, 'granted');
  c.observe(10_000, 1, true, cameraObservation(true, 'ready'));
  c.observe(11_000, 1, true, cameraObservation(true, 'ready'));
  assert.equal(c.snapshot().camera, 'granted');
  assert.equal(c.snapshot().in_frame_seconds, 1);
  assert.equal(faceIndicator(true, 0), 'yellow');
});
test('actual detector failure remains unavailable after a later recovery', () => {
  const c = new ProctoringCounters(0);
  c.observe(0, 0, true, cameraObservation(true, 'loading'));
  c.observe(10_000, 0, true, cameraObservation(true, 'failed'));
  c.observe(11_000, 1, true, cameraObservation(true, 'ready'));
  assert.equal(c.snapshot().camera, 'unavailable');
});
