package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
)

type testServer struct {
	b          *Bridge
	s          *httptest.Server
	c          contextInput
	configPath string
}

func TestEventsReplayAuthorizedMessageAndResume(t *testing.T) {
	ts := newTestServer(t)
	var sent Message
	in := sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Text: "event replay fixture", Files: []File{}}
	if ts.call(t, "/internal/chat/send", in, string(ts.b.serviceToken), &sent) != 200 {
		t.Fatal("send failed")
	}
	var page struct {
		Events       []ChatEvent `json:"events"`
		NextSequence int64       `json:"next_sequence"`
	}
	context := ts.c
	context.ActorID = context.CandidateID
	if ts.call(t, "/internal/chat/events", historyInput{contextInput: context, Limit: 100}, string(ts.b.serviceToken), &page) != 200 {
		t.Fatal("events failed")
	}
	found := false
	for _, event := range page.Events {
		if event.Type == "message.created" && event.Message != nil && event.Message.ID == sent.ID {
			found = event.Message.Text == in.Text
		}
	}
	if !found {
		t.Fatal("authorized message absent")
	}
	if ts.call(t, "/internal/chat/events", historyInput{contextInput: context, AfterSequence: page.NextSequence, Limit: 100}, string(ts.b.serviceToken), &page) != 200 || len(page.Events) != 0 {
		t.Fatal("resume replayed events")
	}
	context.ActorID = "outsider"
	if ts.call(t, "/internal/chat/events", historyInput{contextInput: context, Limit: 100}, string(ts.b.serviceToken), nil) != 403 {
		t.Fatal("outsider access")
	}
}

func newTestServer(t *testing.T) *testServer {
	t.Helper()
	dir := t.TempDir()
	configPath := filepath.Join(dir, "qgramm.toml")
	config := fmt.Sprintf(`[server]
listen = "127.0.0.1:18080"
allow_insecure_loopback = true
[storage]
path = %q
files = %q
[security]
issuer = "comit-qgramm"
audience = "comit-chat"
token_public_key_env = "CHAT_Q_TOKEN_PUBLIC_KEY"
management_secret_env = "QGRAMM_MANAGEMENT_SECRET"
master_key_env = "QGRAMM_MASTER_KEY"
hpke_key_env = "QGRAMM_HPKE_KEY"
[features]
files = true
[capacity]
expected_concurrent_users = 8
[policy]
history = "all"
delete_mode = "global"
reaction_types = []
max_message_bytes = 131072
max_batch = 16
max_file_bytes = 8388860
max_chunk_bytes = 1048576
max_storage_bytes = 1073741824
event_retention_hours = 24
dedup_retention_hours = 24
upload_ttl_hours = 24
`, filepath.Join(dir, "qgramm.sqlite"), filepath.Join(dir, "files"))
	if err := os.WriteFile(configPath, []byte(config), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(serviceToken, "test-service-token-with-enough-entropy")
	t.Setenv(deviceSecret, base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x22}, 32)))
	t.Setenv(signingKeyEnv, base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x33}, 32)))
	t.Setenv("QGRAMM_MANAGEMENT_SECRET", "test-q-management-secret-with-enough-entropy")
	t.Setenv("QGRAMM_MASTER_KEY", base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x44}, 32)))
	t.Setenv("QGRAMM_HPKE_KEY", base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x55}, 32)))
	b, err := New(configPath)
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(b.Handler())
	fixture := &testServer{b: b, s: ts, c: contextInput{ProcessID: "process-a", OwnerID: "owner-a", CandidateID: "candidate-a", ActorID: "owner-a"}, configPath: configPath}
	t.Cleanup(func() { fixture.s.Close(); _ = fixture.b.Close() })
	return fixture
}

func (ts *testServer) call(t *testing.T, path string, body any, token string, out any) int {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(http.MethodPost, ts.s.URL+path, bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", token)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if out != nil || res.StatusCode >= 300 {
		raw, _ := io.ReadAll(res.Body)
		if res.StatusCode < 300 {
			if err := json.Unmarshal(raw, out); err != nil {
				t.Fatalf("status %d: %v (%s)", res.StatusCode, err, raw)
			}
		} else {
			t.Logf("status %d body %s", res.StatusCode, raw)
		}
	}
	return res.StatusCode
}

func TestBridgeMessagesArePrivateAndDurable(t *testing.T) {
	ts := newTestServer(t)
	if got := ts.call(t, "/internal/chat/history", historyInput{contextInput: ts.c, Limit: 10}, "wrong", nil); got != 401 {
		t.Fatalf("wrong token status %d", got)
	}
	key := uuid.NewString()
	var sent Message
	if got := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: key, Text: "private hello"}, "test-service-token-with-enough-entropy", &sent); got != 200 || sent.SenderID != ts.c.OwnerID {
		t.Fatalf("send status=%d message=%+v", got, sent)
	}
	var history struct {
		Messages []Message `json:"messages"`
		Next     int64     `json:"next_sequence"`
	}
	candidate := ts.c
	candidate.ActorID = candidate.CandidateID
	if got := ts.call(t, "/internal/chat/history", historyInput{contextInput: candidate, Limit: 10}, "test-service-token-with-enough-entropy", &history); got != 200 || len(history.Messages) != 1 || history.Messages[0].Text != "private hello" {
		t.Fatalf("history status=%d: %+v", got, history)
	}
	if got := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: key, Text: "different"}, "test-service-token-with-enough-entropy", nil); got != 409 {
		t.Fatalf("conflicting retry status %d", got)
	}
	if _, err := ts.b.core.DB.Exec(`DELETE FROM operations WHERE operation_id=?`, key); err != nil {
		t.Fatal(err)
	}
	if got := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: key, Text: "private hello"}, "test-service-token-with-enough-entropy", nil); got != 200 {
		t.Fatalf("durable retry status %d", got)
	}
	var stored []byte
	if err := ts.b.core.DB.QueryRow(`SELECT payload FROM messages`).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(stored, []byte("private hello")) {
		t.Fatal("message plaintext persisted")
	}
	if history.Next < 1 {
		t.Fatal("missing sequence cursor")
	}
	if got := ts.call(t, "/internal/chat/history", historyInput{contextInput: contextInput{ProcessID: "other", OwnerID: ts.c.OwnerID, CandidateID: ts.c.CandidateID, ActorID: ts.c.OwnerID}, Limit: 10}, "test-service-token-with-enough-entropy", &history); got != 200 || len(history.Messages) != 0 {
		t.Fatalf("cross-process history status=%d: %+v", got, history)
	}
}

func TestBridgeFileRoundTripAndForeignIsolation(t *testing.T) {
	ts := newTestServer(t)
	data := append(bytes.Repeat([]byte("encrypted attachment "), 60000), []byte("tail")...)
	sha := sha256.Sum256(data)
	up := uploadInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Name: "note.txt", MediaType: "text/plain", DataBase64: base64.StdEncoding.EncodeToString(data)}
	var file File
	if got := ts.call(t, "/internal/chat/upload", up, "test-service-token-with-enough-entropy", &file); got != 200 || file.Size != int64(len(data)) {
		t.Fatalf("upload status=%d file=%+v", got, file)
	}
	if file.SHA256 != fmt.Sprintf("%x", sha) {
		t.Fatalf("checksum %s", file.SHA256)
	}
	if got := ts.call(t, "/internal/chat/upload", uploadInput{contextInput: ts.c, IdempotencyKey: up.IdempotencyKey, Name: "renamed.txt", MediaType: up.MediaType, DataBase64: up.DataBase64}, "test-service-token-with-enough-entropy", nil); got != 409 {
		t.Fatalf("conflicting upload status %d", got)
	}
	// Publish beyond the first 100 messages: a single first-page lookup loses this file.
	for i := 0; i < 101; i++ {
		if status := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Text: fmt.Sprintf("earlier message %d", i)}, "test-service-token-with-enough-entropy", nil); status != 200 {
			t.Fatalf("earlier message status %d", status)
		}
	}
	if got := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Text: "file", Files: []File{file}}, "test-service-token-with-enough-entropy", nil); got != 200 {
		t.Fatalf("file send status %d", got)
	}
	// The file must remain available after its message leaves the first history page.
	for i := 0; i < 101; i++ {
		if status := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Text: fmt.Sprintf("later message %d", i)}, "test-service-token-with-enough-entropy", nil); status != 200 {
			t.Fatalf("later message status %d", status)
		}
	}
	if err := filepath.WalkDir(filepath.Join(filepath.Dir(ts.configPath), "files"), func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		stored, err := os.ReadFile(path)
		if err == nil && bytes.Contains(stored, []byte("encrypted attachment ")) {
			return fmt.Errorf("file plaintext persisted")
		}
		return err
	}); err != nil {
		t.Fatal(err)
	}
	// Recreate the complete bridge against the same persistent database and files.
	ts.s.Close()
	if err := ts.b.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := New(ts.configPath)
	if err != nil {
		t.Fatal(err)
	}
	ts.b = restarted
	ts.s = httptest.NewServer(restarted.Handler())
	var got downloadOutput
	candidate := ts.c
	candidate.ActorID = candidate.CandidateID
	if status := ts.call(t, "/internal/chat/download", downloadInput{contextInput: candidate, FileID: file.ID}, "test-service-token-with-enough-entropy", &got); status != 200 {
		t.Fatalf("download status %d", status)
	}
	decoded, err := base64.StdEncoding.DecodeString(got.DataBase64)
	if err != nil || !bytes.Equal(decoded, data) {
		t.Fatalf("download data %q", decoded)
	}
	foreign := downloadInput{contextInput: contextInput{ProcessID: "foreign", OwnerID: ts.c.OwnerID, CandidateID: ts.c.CandidateID, ActorID: ts.c.OwnerID}, FileID: file.ID}
	if status := ts.call(t, "/internal/chat/download", foreign, "test-service-token-with-enough-entropy", nil); status != 404 {
		t.Fatalf("foreign download status %d", status)
	}
}

func TestBridgeHealthDoesNotExposeQRoutes(t *testing.T) {
	ts := newTestServer(t)
	res, err := http.Get(ts.s.URL + "/v1/chats/x/messages")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatalf("raw route status %d", res.StatusCode)
	}
	res, err = http.Get(ts.s.URL + "/readyz")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		t.Fatalf("ready status %d", res.StatusCode)
	}
}

func TestBridgeMaximumFileIncludesProtocolOverhead(t *testing.T) {
	ts := newTestServer(t)
	data := bytes.Repeat([]byte{0x6a}, 8*1024*1024)
	var file File
	if status := ts.call(t, "/internal/chat/upload", uploadInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Name: "maximum.bin", MediaType: "application/octet-stream", DataBase64: base64.StdEncoding.EncodeToString(data)}, "test-service-token-with-enough-entropy", &file); status != 200 || file.Size != int64(len(data)) {
		t.Fatalf("maximum upload status=%d size=%d", status, file.Size)
	}
	if status := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Files: []File{file}}, "test-service-token-with-enough-entropy", nil); status != 200 {
		t.Fatalf("maximum attachment status %d", status)
	}
	var downloaded downloadOutput
	if status := ts.call(t, "/internal/chat/download", downloadInput{contextInput: ts.c, FileID: file.ID}, "test-service-token-with-enough-entropy", &downloaded); status != 200 {
		t.Fatalf("maximum download status %d", status)
	}
	decoded, err := base64.StdEncoding.DecodeString(downloaded.DataBase64)
	if err != nil || !bytes.Equal(decoded, data) {
		t.Fatal("maximum file did not round trip")
	}
}

func TestBridgeRestartKeepsChatHistory(t *testing.T) {
	ts := newTestServer(t)
	if status := ts.call(t, "/internal/chat/send", sendInput{contextInput: ts.c, IdempotencyKey: uuid.NewString(), Text: "survives restart"}, "test-service-token-with-enough-entropy", nil); status != 200 {
		t.Fatalf("send status %d", status)
	}
	ts.s.Close()
	if err := ts.b.Close(); err != nil {
		t.Fatal(err)
	}
	b, err := New(ts.configPath)
	if err != nil {
		t.Fatal(err)
	}
	ts.b = b
	ts.s = httptest.NewServer(b.Handler())
	var history struct {
		Messages []Message `json:"messages"`
	}
	if status := ts.call(t, "/internal/chat/history", historyInput{contextInput: ts.c, Limit: 10}, "test-service-token-with-enough-entropy", &history); status != 200 || len(history.Messages) != 1 || history.Messages[0].Text != "survives restart" {
		t.Fatalf("history after restart status=%d: %+v", status, history)
	}
}
