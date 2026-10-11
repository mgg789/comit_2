# Face detection assets

BlazeFace short-range float16 model, revision 1, from Google's MediaPipe model repository:
https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite

SHA-256: b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f

The adjacent MediaPipe WASM files are copied unmodified from the pinned
@mediapipe/tasks-vision 0.10.32 package (Apache-2.0).
Face detection runs locally. No frame is sent to an external inference service.
Official integration guide: https://developers.google.com/edge/mediapipe/solutions/vision/face_detector/web_js
