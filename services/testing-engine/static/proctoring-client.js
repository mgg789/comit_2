/**
 * Panoptikon reference client (docs/testing-design.md §5).
 *
 * Tracks window focus (Page Visibility API) and runs YuNet face detection
 * entirely in the browser via onnxruntime-web: frames are processed locally
 * and never uploaded. Only aggregate counters are POSTed to
 * /testing/sessions/{id}/supervision when the session ends.
 *
 * Usage (apps/testing integration):
 *   const p = await startPanoptikon({ sessionId, uploadUrl: "/api/testing/..." });
 *   ... p.stop()  // uploads aggregates, returns the server badge
 *
 * Consent must be collected BEFORE start(): the counters are always uploaded
 * (aggregates only); raw capture happens exclusively when the operator turns
 * the recording contour on (TESTING_RECORDING_MODE=full server-side).
 */

export async function startPanoptikon({ sessionId, uploadUrl, modelUrl = "/models/yunet.onnx" }) {
  const state = {
    totalSeconds: 0, inFrameSeconds: 0, focusLostSeconds: 0,
    multiFaceEvents: 0, camera: "granted", lastTick: Date.now(),
  };
  const onVisibility = () => {
    if (document.hidden) state.focusLostSeconds += (Date.now() - state.lastTick) / 1000;
    state.lastTick = Date.now();
  };
  document.addEventListener("visibilitychange", onVisibility);

  let video = null;
  let session = null;
  let raf = null;
  let facePresent = false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320 } });
    video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    const ort = await import("onnxruntime-web");
    session = await ort.InferenceSession.create(modelUrl, { executionProviders: ["wasm"] });
    const tick = async () => {
      state.totalSeconds = (Date.now() - state.lastTick) / 1000;
      if (!document.hidden && video.readyState >= 2 && session) {
        const faces = await detectFaces(ort, session, video);
        if (faces >= 1) state.inFrameSeconds += 1;
        if (faces >= 2) state.multiFaceEvents += 1;
        facePresent = faces >= 1;
      }
      state.lastTick = Date.now();
      raf = setTimeout(tick, 1000);
    };
    tick();
  } catch {
    state.camera = "denied";
  }

  async function detectFaces(ort, inferenceSession, video) {
    const canvas = document.createElement("canvas");
    canvas.width = 320; canvas.height = Math.max(1, Math.round(320 * video.videoHeight / (video.videoWidth || 320)));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    const data = new Float32Array(canvas.width * canvas.height * 3);
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 0, j = 0; i < pixels.length; i += 4, j += 3) {
      data[j] = pixels[i] / 255; data[j + 1] = pixels[i + 1] / 255; data[j + 2] = pixels[i + 2] / 255;
    }
    try {
      const output = await inferenceSession.run(
        { input: new ort.Tensor("float32", data, [1, 3, canvas.height, canvas.width]) });
      return YuNetPostprocess(output, 0.6);
    } catch {
      return 0;
    }
  }

  return {
    get aggregates() {
      return { total_seconds: Math.round(state.totalSeconds),
               in_frame_seconds: Math.round(state.inFrameSeconds),
               focus_lost_seconds: Math.round(state.focusLostSeconds),
               multi_face_events: state.multiFaceEvents,
               camera: state.camera };
    },
    facePresent: () => facePresent,
    async stop() {
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(raf);
      video?.srcObject?.getTracks().forEach(track => track.stop());
      await session?.release?.();
      const response = await fetch(uploadUrl.replace("{sessionId}", sessionId), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(this.aggregates) });
      return response.json();
    },
  };
}

/** Minimal YuNet output decode: confidence tensor rows -> face count. */
function YuNetPostprocess(output, threshold) {
  const tensor = Object.values(output)[0];
  if (!tensor || !tensor.data) return 0;
  let faces = 0;
  for (let i = 0; i < tensor.data.length; i += 15) {
    if (tensor.data[i] >= threshold) faces += 1;
  }
  return faces;
}
