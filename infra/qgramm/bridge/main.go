// Command bridge exposes the narrow Comit chat contract over a private HTTP
// listener while keeping QGramm's protocol and raw routes process-private.
package main

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/cloudflare/circl/hpke"
	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/mgg789/QGramm/internal/config"
	"github.com/mgg789/QGramm/internal/core"
	"github.com/mgg789/QGramm/internal/cryptoenc"
	_ "github.com/mgg789/QGramm/internal/modules"
)

const (
	maxBody = 12 << 20
	maxFile = 8 << 20
	// QGramm caps the encrypted wire chunk at 1 MiB; AES-GCM adds a 12-byte
	// nonce and a 16-byte tag around each plaintext chunk.
	chunkSize     = (1 << 20) - 28
	maxHistory    = 100
	maxTextBytes  = 64 << 10
	serviceToken  = "CHAT_SERVICE_TOKEN"
	deviceSecret  = "CHAT_DEVICE_SECRET"
	signingKeyEnv = "CHAT_SIGNING_KEY"
)

// File describes the only attachment metadata exposed by the Comit API.
type File struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	MediaType string `json:"media_type"`
	Size      int64  `json:"size"`
	SHA256    string `json:"sha256"`
}

// Message is the decrypted, intentionally small Comit chat projection.
type Message struct {
	ID        string `json:"id"`
	Sequence  int64  `json:"sequence"`
	SenderID  string `json:"sender_id"`
	CreatedAt string `json:"created_at"`
	Text      string `json:"text"`
	Files     []File `json:"files"`
}

type contextInput struct {
	ProcessID   string `json:"process_id"`
	OwnerID     string `json:"owner_id"`
	CandidateID string `json:"candidate_id"`
	ActorID     string `json:"actor_id"`
}

type historyInput struct {
	contextInput
	AfterSequence int64 `json:"after_sequence"`
	Limit         int   `json:"limit"`
}

// ChatEvent exposes only authorized messages; other journal rows advance the cursor.
type ChatEvent struct {
	Sequence int64    `json:"sequence"`
	Type     string   `json:"type"`
	Message  *Message `json:"message,omitempty"`
}

type sendInput struct {
	contextInput
	IdempotencyKey string `json:"idempotency_key"`
	Text           string `json:"text"`
	Files          []File `json:"files"`
}

type uploadInput struct {
	contextInput
	IdempotencyKey string `json:"idempotency_key"`
	Name           string `json:"name"`
	MediaType      string `json:"media_type"`
	DataBase64     string `json:"data_base64"`
}

type downloadInput struct {
	contextInput
	FileID string `json:"file_id"`
}

type downloadOutput struct {
	ID         string `json:"id"`
	DataBase64 string `json:"data_base64"`
	Name       string `json:"name"`
	MediaType  string `json:"media_type"`
	Size       int64  `json:"size"`
	SHA256     string `json:"sha256"`
}

type payload struct {
	Text  string `json:"text"`
	Files []File `json:"files"`
}

type actor struct {
	UserID   string
	DeviceID string
	Seed     []byte
}

// Bridge owns one QGramm core and serializes lazy identity/chat provisioning.
type Bridge struct {
	core         *core.Core
	serviceToken []byte
	deviceSecret []byte
	signing      ed25519.PrivateKey
	management   string
	issuer       string
	audience     string
	mu           sync.Mutex
	provisioned  map[string]bool
}

// New loads config, derives the Q token public key from CHAT_SIGNING_KEY, and
// opens the durable QGramm core. It never starts QGramm's listener.
func New(configPath string) (*Bridge, error) {
	cfg, err := config.Load(configPath)
	if err != nil {
		return nil, err
	}
	token, err := requiredSecret(serviceToken, 32)
	if err != nil {
		return nil, err
	}
	device, err := encodedSecret(deviceSecret, 32)
	if err != nil {
		return nil, err
	}
	seed, err := encodedSecret(signingKeyEnv, ed25519.SeedSize)
	if err != nil {
		return nil, err
	}
	key := ed25519.NewKeyFromSeed(seed)
	if err := os.Setenv(cfg.Security.TokenPublicKeyEnv, base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey))); err != nil {
		return nil, err
	}
	management := os.Getenv(cfg.Security.ManagementSecretEnv)
	if len(management) < 32 {
		return nil, errors.New("QGramm management secret must contain at least 32 characters")
	}
	c, err := core.Open(cfg, []string{"files"})
	if err != nil {
		return nil, err
	}
	return &Bridge{core: c, serviceToken: token, deviceSecret: device, signing: key, management: management, issuer: cfg.Security.Issuer, audience: cfg.Security.Audience, provisioned: make(map[string]bool)}, nil
}

// Close stops QGramm workers and closes its durable database.
func (b *Bridge) Close() error {
	if b == nil || b.core == nil {
		return nil
	}
	return b.core.Close()
}

// Handler returns the private Comit HTTP handler. Callers must bind it only to
// the internal listener; QGramm's own handler is never returned here.
func (b *Bridge) Handler() http.Handler { return http.HandlerFunc(b.serveHTTP) }

func (b *Bridge) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
		if r.Method != http.MethodGet {
			writeError(w, 405, "method not allowed")
			return
		}
		if r.URL.Path == "/readyz" {
			if err := b.core.DB.PingContext(r.Context()); err != nil {
				writeError(w, 503, "storage unavailable")
				return
			}
		}
		writeJSON(w, 200, map[string]string{"status": "ok"})
		return
	}
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Internal-Token")), b.serviceToken) != 1 {
		writeError(w, 401, "invalid internal token")
		return
	}
	if r.Method != http.MethodPost {
		writeError(w, 405, "method not allowed")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxBody)
	switch r.URL.Path {
	case "/internal/chat/history":
		b.handleHistory(w, r)
	case "/internal/chat/events":
		b.handleEvents(w, r)
	case "/internal/chat/send":
		b.handleSend(w, r)
	case "/internal/chat/upload":
		b.handleUpload(w, r)
	case "/internal/chat/download":
		b.handleDownload(w, r)
	default:
		writeError(w, 404, "not found")
	}
}

// handleEvents reads one bounded durable journal page, never a held-open stream.
func (b *Bridge) handleEvents(w http.ResponseWriter, r *http.Request) {
	var in historyInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if in.AfterSequence < 0 || in.AfterSequence > 9007199254740991 || in.Limit < 1 || in.Limit > maxHistory {
		writeError(w, 400, "invalid event cursor")
		return
	}
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	// Direct Core.Events retains the same current member/device ACL as its HTTP handler.
	raw, err := b.core.Events(r.Context(), core.Identity{UserID: a.UserID, DeviceID: a.DeviceID}, chat, in.AfterSequence, in.Limit)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	out := make([]ChatEvent, 0, len(raw))
	next := in.AfterSequence
	for _, event := range raw {
		if event.Seq > 9007199254740991 {
			writeError(w, 502, "event sequence exceeds contract")
			return
		}
		item := ChatEvent{Sequence: event.Seq, Type: "cursor"}
		if event.Type == "message.created" {
			encoded, e := json.Marshal(event.Data)
			var message core.Message
			if e != nil || json.Unmarshal(encoded, &message) != nil {
				writeError(w, 502, "invalid event projection")
				return
			}
			plain, e := b.openMessage(r.Context(), a, message)
			if e != nil {
				writeError(w, 502, "event decryption failed")
				return
			}
			projected := projection(message, plain, in.contextInput, b)
			item.Type = "message.created"
			item.Message = &projected
		}
		out = append(out, item)
		if event.Seq > next {
			next = event.Seq
		}
	}
	writeJSON(w, 200, map[string]any{"events": out, "next_sequence": next})
}

func (b *Bridge) handleHistory(w http.ResponseWriter, r *http.Request) {
	var in historyInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if in.AfterSequence < 0 || in.Limit < 1 || in.Limit > maxHistory {
		writeError(w, 400, "invalid history cursor")
		return
	}
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	res, err := b.qCall(r.Context(), a, "GET", "/v1/chats/"+chat+"/messages?after="+strconv.FormatInt(in.AfterSequence, 10)+"&limit="+strconv.Itoa(in.Limit), nil, "")
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if res.status != 200 {
		writeQResponse(w, res)
		return
	}
	var raw []core.Message
	if err := json.Unmarshal(res.body, &raw); err != nil {
		writeError(w, 502, "invalid QGramm history")
		return
	}
	out := make([]Message, 0, len(raw))
	next := in.AfterSequence
	for _, m := range raw {
		p, err := b.openMessage(r.Context(), a, m)
		if err != nil {
			writeError(w, 502, "message decryption failed")
			return
		}
		out = append(out, projection(m, p, in.contextInput, b))
		if m.Seq > next {
			next = m.Seq
		}
	}
	writeJSON(w, 200, map[string]any{"messages": out, "next_sequence": next})
}

func (b *Bridge) handleSend(w http.ResponseWriter, r *http.Request) {
	var in sendInput
	if !decodeJSON(w, r, &in) {
		return
	}
	parsedKey, err := uuid.Parse(in.IdempotencyKey)
	if err != nil || len([]byte(in.Text)) > maxTextBytes || len(in.Files) > 8 || (strings.TrimSpace(in.Text) == "" && len(in.Files) == 0) {
		writeError(w, 400, "invalid message")
		return
	}
	in.IdempotencyKey = parsedKey.String()
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if err := b.validateFiles(r.Context(), a, chat, in.Files); err != nil {
		writeAPIError(w, err)
		return
	}
	p := payload{Text: in.Text, Files: append([]File(nil), in.Files...)}
	plain, _ := json.Marshal(p)
	if previous, ok, e := b.previousMessage(r.Context(), a, in.IdempotencyKey); e != nil {
		writeAPIError(w, e)
		return
	} else if ok {
		previous, e = b.core.ViewMessage(r.Context(), core.Identity{UserID: a.UserID, DeviceID: a.DeviceID}, previous.ID)
		if e != nil {
			writeAPIError(w, e)
			return
		}
		old, e := b.openMessage(r.Context(), a, previous)
		if e != nil {
			writeError(w, 502, "message decryption failed")
			return
		}
		oldPlain, _ := json.Marshal(old)
		if !bytes.Equal(oldPlain, plain) {
			writeError(w, 409, "idempotency key reused with different content")
			return
		}
		writeJSON(w, 200, projection(previous, old, in.contextInput, b))
		return
	}
	env, err := cryptoenc.SealEnvelope(b.qPublicKey(), plain, cryptoenc.Binding(chat, a.UserID, a.DeviceID, in.IdempotencyKey))
	if err != nil {
		writeError(w, 500, "message encryption failed")
		return
	}
	m, err := b.core.Send(r.Context(), core.Identity{UserID: a.UserID, DeviceID: a.DeviceID}, chat, core.MessageInput{OperationID: in.IdempotencyKey, Envelope: &env, Attachments: fileIDs(in.Files)})
	if err != nil {
		writeAPIError(w, err)
		return
	}
	// Send returns a projected message for the sender; reprojecting also makes
	// retries use the durable encrypted payload rather than request memory.
	m, err = b.core.ViewMessage(r.Context(), core.Identity{UserID: a.UserID, DeviceID: a.DeviceID}, m.ID)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	p, err = b.openMessage(r.Context(), a, m)
	if err != nil {
		writeError(w, 502, "message decryption failed")
		return
	}
	writeJSON(w, 200, projection(m, p, in.contextInput, b))
}

func (b *Bridge) handleUpload(w http.ResponseWriter, r *http.Request) {
	var in uploadInput
	if !decodeJSON(w, r, &in) {
		return
	}
	parsedKey, err := uuid.Parse(in.IdempotencyKey)
	if err != nil {
		writeError(w, 400, "invalid idempotency key")
		return
	}
	in.IdempotencyKey = parsedKey.String()
	data, err := base64.StdEncoding.DecodeString(in.DataBase64)
	if err != nil || len(data) == 0 || len(data) > maxFile || !validMetadata(in.Name, in.MediaType) {
		writeError(w, 400, "invalid file")
		return
	}
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	digest := sha256.Sum256(data)
	sha := hex.EncodeToString(digest[:])
	chunks := (len(data) + chunkSize - 1) / chunkSize
	operation := in.IdempotencyKey
	fileKey := hmacBytes(b.deviceSecret, "file/v1/"+in.ProcessID+"/"+a.UserID+"/"+operation+"/"+in.Name+"/"+in.MediaType+"/"+sha+"/"+strconv.Itoa(len(data)))
	wires := make([][]byte, 0, chunks)
	var qSize int
	qHash := sha256.New()
	for index := 0; index < len(data); index += chunkSize {
		end := index + chunkSize
		if end > len(data) {
			end = len(data)
		}
		wire, e := encryptChunk(fileKey, len(wires), chat, a, operation, data[index:end])
		if e != nil {
			writeError(w, 500, "file encryption failed")
			return
		}
		wires = append(wires, wire)
		qSize += len(wire)
		_, _ = qHash.Write(wire)
	}
	env, err := cryptoenc.SealEnvelope(b.qPublicKey(), fileKey, cryptoenc.Binding(chat, a.UserID, a.DeviceID, operation))
	if err != nil {
		writeError(w, 500, "file encryption failed")
		return
	}
	body, _ := json.Marshal(map[string]any{"operation_id": operation, "size": qSize, "chunks": chunks, "sha256": hex.EncodeToString(qHash.Sum(nil)), "envelope": env})
	res, err := b.qCall(r.Context(), a, http.MethodPost, "/v1/chats/"+chat+"/uploads", body, "application/json")
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if res.status != 200 && res.status != 201 {
		writeQResponse(w, res)
		return
	}
	var created struct {
		ID    string `json:"id"`
		State string `json:"state"`
	}
	if json.Unmarshal(res.body, &created) != nil || created.ID == "" {
		writeError(w, 502, "invalid QGramm upload")
		return
	}
	if created.State != "ready" {
		for part, wire := range wires {
			e := error(nil)
			h := sha256.Sum256(wire)
			res, e = b.qCallHeaders(r.Context(), a, http.MethodPut, "/v1/uploads/"+created.ID+"/chunks/"+strconv.Itoa(part), wire, "application/octet-stream", map[string]string{"X-Chunk-SHA256": hex.EncodeToString(h[:])})
			if e != nil {
				writeAPIError(w, e)
				return
			}
			if res.status != 200 && res.status != 201 {
				writeQResponse(w, res)
				return
			}
		}
		res, err = b.qCall(r.Context(), a, http.MethodPost, "/v1/uploads/"+created.ID+"/complete", nil, "")
		if err != nil {
			writeAPIError(w, err)
			return
		}
		if res.status != 200 {
			writeQResponse(w, res)
			return
		}
	}
	writeJSON(w, 200, File{ID: created.ID, Name: in.Name, MediaType: in.MediaType, Size: int64(len(data)), SHA256: sha})
}

func (b *Bridge) handleDownload(w http.ResponseWriter, r *http.Request) {
	var in downloadInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if in.FileID == "" {
		writeError(w, 400, "file_id required")
		return
	}
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	var found File
	var uploadMessage bool
	var uploadActor actor
	var uploadOperation string
	var messageID string
	err = b.core.DB.QueryRowContext(r.Context(), `SELECT msg.id FROM upload_messages link JOIN messages msg ON msg.id=link.message_id WHERE link.upload_id=? AND msg.chat_id=? AND msg.deleted=0 ORDER BY msg.seq LIMIT 1`, in.FileID, chat).Scan(&messageID)
	if errors.Is(err, sql.ErrNoRows) {
		writeError(w, 404, "file not published in this chat")
		return
	}
	if err != nil {
		writeError(w, 502, "file lookup failed")
		return
	}
	m, err := b.core.ViewMessage(r.Context(), core.Identity{UserID: a.UserID, DeviceID: a.DeviceID}, messageID)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	p, err := b.openMessage(r.Context(), a, m)
	if err != nil {
		writeError(w, 502, "message decryption failed")
		return
	}
	for _, f := range p.Files {
		if f.ID == in.FileID {
			found, uploadMessage = f, true
		}
	}
	if m.Sender == b.actor(in.ProcessID, "owner", in.OwnerID).UserID {
		uploadActor = b.actor(in.ProcessID, "owner", in.OwnerID)
	} else {
		uploadActor = b.actor(in.ProcessID, "candidate", in.CandidateID)
	}
	if !uploadMessage {
		writeError(w, 404, "file not published in this chat")
		return
	}
	statusResponse, err := b.qCall(r.Context(), a, http.MethodGet, "/v1/uploads/"+found.ID, nil, "")
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if statusResponse.status != 200 {
		writeQResponse(w, statusResponse)
		return
	}
	var uploadStatus struct {
		Binding struct {
			OperationID string `json:"operation_id"`
		} `json:"chunk_binding"`
	}
	if json.Unmarshal(statusResponse.body, &uploadStatus) != nil || uploadStatus.Binding.OperationID == "" {
		writeError(w, 502, "invalid QGramm upload metadata")
		return
	}
	uploadOperation = uploadStatus.Binding.OperationID
	var data bytes.Buffer
	keyResponse, err := b.qCall(r.Context(), a, http.MethodGet, "/v1/uploads/"+found.ID+"/key", nil, "")
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if keyResponse.status != 200 {
		writeQResponse(w, keyResponse)
		return
	}
	var keyEnvelope struct {
		Envelope cryptoenc.Envelope `json:"envelope"`
	}
	if json.Unmarshal(keyResponse.body, &keyEnvelope) != nil {
		writeError(w, 502, "invalid QGramm file key")
		return
	}
	fileKey, err := openDeviceEnvelope(keyEnvelope.Envelope, a.Seed, cryptoenc.Binding(chat, a.UserID, a.DeviceID, found.ID))
	if err != nil || len(fileKey) != 32 {
		writeError(w, 502, "file key decryption failed")
		return
	}
	for i := int64(0); i < (found.Size+chunkSize-1)/chunkSize; i++ {
		res, e := b.qCall(r.Context(), a, http.MethodGet, "/v1/uploads/"+found.ID+"/chunks/"+strconv.FormatInt(i, 10), nil, "")
		if e != nil {
			writeAPIError(w, e)
			return
		}
		if res.status != 200 {
			writeQResponse(w, res)
			return
		}
		plainChunk, e := decryptChunk(fileKey, int(i), chat, uploadActor, uploadOperation, res.body)
		if e != nil {
			writeError(w, 502, "file decryption failed")
			return
		}
		_, _ = data.Write(plainChunk)
	}
	plain := data.Bytes()
	sum := sha256.Sum256(plain)
	if int64(len(plain)) != found.Size || hex.EncodeToString(sum[:]) != found.SHA256 {
		writeError(w, 502, "file integrity check failed")
		return
	}
	writeJSON(w, 200, downloadOutput{ID: found.ID, DataBase64: base64.StdEncoding.EncodeToString(plain), Name: found.Name, MediaType: found.MediaType, Size: found.Size, SHA256: found.SHA256})
}

func (b *Bridge) context(ctx context.Context, in contextInput) (actor, string, error) {
	if !validContext(in) {
		return actor{}, "", apiErr{400, "invalid chat context"}
	}
	if in.ActorID != in.OwnerID && in.ActorID != in.CandidateID {
		return actor{}, "", apiErr{403, "actor is not a chat member"}
	}
	role, account := "owner", in.OwnerID
	if in.ActorID == in.CandidateID {
		role, account = "candidate", in.CandidateID
	}
	a := b.actor(in.ProcessID, role, account)
	chat := stableID("chat", in.ProcessID, in.OwnerID, in.CandidateID)
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.provisioned[chat] {
		if err := b.provision(ctx, in, chat); err != nil {
			return actor{}, "", err
		}
		b.provisioned[chat] = true
	}
	return a, chat, nil
}

func (b *Bridge) actor(process, role, account string) actor {
	seed := hmacBytes(b.deviceSecret, "device/v1/"+process+"/"+role+"/"+account)
	return actor{UserID: stableID("user", process, role, account), DeviceID: stableID("device", process, role, account), Seed: seed}
}

func (b *Bridge) provision(ctx context.Context, in contextInput, chat string) error {
	for _, x := range []struct{ role, account string }{{"owner", in.OwnerID}, {"candidate", in.CandidateID}} {
		a := b.actor(in.ProcessID, x.role, x.account)
		for _, path := range []string{"/management/v1/users/" + a.UserID, "/management/v1/users/" + a.UserID + "/devices/" + a.DeviceID} {
			var body []byte
			if strings.Contains(path, "/devices/") {
				body, _ = json.Marshal(map[string]string{"public_key": base64.StdEncoding.EncodeToString(devicePublic(a.Seed))})
			} else {
				body = []byte(`{"disabled":false}`)
			}
			res, err := b.managementCall(ctx, http.MethodPut, path, body)
			if err != nil {
				return err
			}
			if res.status != 200 {
				return qErr(res)
			}
		}
	}
	members := []string{b.actor(in.ProcessID, "owner", in.OwnerID).UserID, b.actor(in.ProcessID, "candidate", in.CandidateID).UserID}
	body, _ := json.Marshal(map[string]any{"id": chat, "mode": "basic", "members": members})
	res, err := b.managementCall(ctx, http.MethodPost, "/management/v1/chats/direct", body)
	if err != nil {
		return err
	}
	if res.status == 201 {
		return nil
	}
	if res.status != 409 {
		return qErr(res)
	}
	rows, err := b.core.DB.QueryContext(ctx, `SELECT user_id FROM members WHERE chat_id=? ORDER BY user_id`, chat)
	if err != nil {
		return err
	}
	defer rows.Close()
	got := []string{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return err
		}
		got = append(got, id)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return err
	}
	rows.Close()
	if len(got) != 2 || !sameStrings(got, members) {
		return errors.New("existing chat has unexpected members")
	}
	var mode, kind string
	if err := b.core.DB.QueryRowContext(ctx, `SELECT mode,kind FROM chats WHERE id=?`, chat).Scan(&mode, &kind); err != nil {
		return err
	}
	if mode != "basic" || kind != "direct" {
		return errors.New("existing chat is not a basic direct chat")
	}
	return nil
}

func (b *Bridge) managementCall(ctx context.Context, method, path string, body []byte) (qResponse, error) {
	return b.dispatch(ctx, method, path, body, b.management, "application/json", nil)
}

type qResponse struct {
	status  int
	body    []byte
	headers http.Header
}

func (b *Bridge) qCall(ctx context.Context, a actor, method, path string, body []byte, contentType string) (qResponse, error) {
	return b.qCallHeaders(ctx, a, method, path, body, contentType, nil)
}

func (b *Bridge) qCallHeaders(ctx context.Context, a actor, method, path string, body []byte, contentType string, headers map[string]string) (qResponse, error) {
	return b.dispatch(ctx, method, path, body, b.jwt(a), contentType, headers)
}

// Dispatch through the real core handlers without a socket or public protocol listener.
func (b *Bridge) dispatch(ctx context.Context, method, path string, body []byte, token, contentType string, headers map[string]string) (qResponse, error) {
	req, err := http.NewRequestWithContext(ctx, method, "http://127.0.0.1"+path, bytes.NewReader(body))
	if err != nil {
		return qResponse{}, err
	}
	req.RemoteAddr = "127.0.0.1:0"
	req.Header.Set("Authorization", "Bearer "+token)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	w := &coreResponse{header: make(http.Header)}
	b.core.Handler().ServeHTTP(w, req)
	if w.err != nil {
		return qResponse{}, w.err
	}
	if w.status == 0 {
		w.status = http.StatusOK
	}
	return qResponse{status: w.status, body: w.body.Bytes(), headers: w.header}, nil
}

// Only in-process projections use this writer; raw encrypted history is bounded independently of the public page.
type coreResponse struct {
	header http.Header
	status int
	body   bytes.Buffer
	err    error
}

func (w *coreResponse) Header() http.Header { return w.header }
func (w *coreResponse) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}
func (w *coreResponse) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	if w.err != nil {
		return 0, w.err
	}
	if w.body.Len()+len(data) > 32<<20 {
		w.err = errors.New("core projection exceeds bound")
		return 0, w.err
	}
	return w.body.Write(data)
}

func (b *Bridge) jwt(a actor) string {
	now := time.Now()
	claims := jwt.MapClaims{"sub": a.UserID, "device_id": a.DeviceID, "iss": b.issuer, "aud": b.audience, "iat": now.Unix(), "exp": now.Add(10 * time.Minute).Unix()}
	t := jwt.NewWithClaims(jwt.SigningMethodEdDSA, claims)
	signed, _ := t.SignedString(b.signing)
	return signed
}

func (b *Bridge) qPublicKey() []byte {
	key, _ := base64.StdEncoding.DecodeString(b.core.Engine.PublicKey())
	return key
}

func (b *Bridge) openMessage(ctx context.Context, a actor, m core.Message) (payload, error) {
	if m.Envelope == nil {
		return payload{}, errors.New("message has no envelope")
	}
	plain, err := openDeviceEnvelope(*m.Envelope, a.Seed, cryptoenc.Binding(m.ChatID, a.UserID, a.DeviceID, m.ID))
	if err != nil {
		return payload{}, err
	}
	var out payload
	if err := json.Unmarshal(plain, &out); err != nil {
		return payload{}, err
	}
	return out, nil
}

func openDeviceEnvelope(envelope cryptoenc.Envelope, seed, binding []byte) ([]byte, error) {
	enc, err := base64.StdEncoding.DecodeString(envelope.Enc)
	if err != nil {
		return nil, err
	}
	cipherText, err := base64.StdEncoding.DecodeString(envelope.Ciphertext)
	if err != nil {
		return nil, err
	}
	private, err := hpke.KEM_X25519_HKDF_SHA256.Scheme().UnmarshalBinaryPrivateKey(seed)
	if err != nil {
		return nil, err
	}
	suite := hpke.NewSuite(hpke.KEM_X25519_HKDF_SHA256, hpke.KDF_HKDF_SHA256, hpke.AEAD_AES128GCM)
	receiver, err := suite.NewReceiver(private, []byte("qgramm/basic/v1"))
	if err != nil {
		return nil, err
	}
	opener, err := receiver.Setup(enc)
	if err != nil {
		return nil, err
	}
	return opener.Open(cipherText, binding)
}

func (b *Bridge) validateFiles(ctx context.Context, a actor, chat string, files []File) error {
	seen := map[string]bool{}
	for _, f := range files {
		if seen[f.ID] || !validMetadata(f.Name, f.MediaType) || f.Size <= 0 || f.Size > maxFile || len(f.SHA256) != sha256.Size*2 {
			return apiErr{400, "invalid attachment metadata"}
		}
		seen[f.ID] = true
		res, err := b.qCall(ctx, a, http.MethodGet, "/v1/uploads/"+f.ID, nil, "")
		if err != nil {
			return err
		}
		if res.status != 200 {
			return qErr(res)
		}
		var status struct {
			State   string `json:"state"`
			Size    int64  `json:"size"`
			SHA256  string `json:"sha256"`
			Binding struct {
				ChatID      string `json:"chat_id"`
				UserID      string `json:"user_id"`
				DeviceID    string `json:"device_id"`
				OperationID string `json:"operation_id"`
			} `json:"chunk_binding"`
		}
		wireSize := f.Size + int64((f.Size+chunkSize-1)/chunkSize)*28
		if json.Unmarshal(res.body, &status) != nil || status.State != "ready" || status.Size != wireSize || status.Binding.ChatID != chat || status.Binding.UserID != a.UserID || status.Binding.DeviceID != a.DeviceID {
			return apiErr{409, "attachment does not match upload"}
		}
		keyResponse, err := b.qCall(ctx, a, http.MethodGet, "/v1/uploads/"+f.ID+"/key", nil, "")
		if err != nil {
			return err
		}
		if keyResponse.status != 200 {
			return qErr(keyResponse)
		}
		var keyEnvelope struct {
			Envelope cryptoenc.Envelope `json:"envelope"`
		}
		if json.Unmarshal(keyResponse.body, &keyEnvelope) != nil {
			return apiErr{502, "invalid file key"}
		}
		fileKey, err := openDeviceEnvelope(keyEnvelope.Envelope, a.Seed, cryptoenc.Binding(chat, a.UserID, a.DeviceID, f.ID))
		if err != nil || len(fileKey) != 32 {
			return apiErr{502, "file key decryption failed"}
		}
		hash := sha256.New()
		var total int64
		for index := int64(0); total < f.Size; index++ {
			chunk, e := b.qCall(ctx, a, http.MethodGet, "/v1/uploads/"+f.ID+"/chunks/"+strconv.FormatInt(index, 10), nil, "")
			if e != nil {
				return e
			}
			if chunk.status != 200 {
				return qErr(chunk)
			}
			plain, e := decryptChunk(fileKey, int(index), chat, a, status.Binding.OperationID, chunk.body)
			if e != nil {
				return apiErr{409, "attachment cannot be decrypted"}
			}
			total += int64(len(plain))
			_, _ = hash.Write(plain)
		}
		if total != f.Size || hex.EncodeToString(hash.Sum(nil)) != f.SHA256 {
			return apiErr{409, "attachment metadata does not match content"}
		}
	}
	return nil
}

func (b *Bridge) previousMessage(ctx context.Context, a actor, operation string) (core.Message, bool, error) {
	var raw []byte
	err := b.core.DB.QueryRowContext(ctx, `SELECT result FROM operations WHERE device_id=? AND operation_id=?`, a.DeviceID, operation).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		var m core.Message
		err = b.core.DB.QueryRowContext(ctx, `SELECT id,chat_id,sender,device_id,operation_id,seq,revision,deleted,epoch,created_at FROM messages WHERE device_id=? AND operation_id=?`, a.DeviceID, operation).Scan(&m.ID, &m.ChatID, &m.Sender, &m.DeviceID, &m.OperationID, &m.Seq, &m.Revision, &m.Deleted, &m.Epoch, &m.CreatedAt)
		if errors.Is(err, sql.ErrNoRows) {
			return core.Message{}, false, nil
		}
		if err != nil {
			return core.Message{}, false, err
		}
		return m, true, nil
	}
	if err != nil {
		return core.Message{}, false, err
	}
	var m core.Message
	if err := json.Unmarshal(raw, &m); err != nil {
		return core.Message{}, false, err
	}
	return m, true, nil
}

func encryptChunk(key []byte, index int, chat string, a actor, operation string, plain []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	copy(nonce, hmacBytes(key, fmt.Sprintf("nonce/%d", index)))
	return gcm.Seal(nonce, nonce, plain, cryptoenc.Binding(chat, a.UserID, a.DeviceID, operation+"/"+strconv.Itoa(index))), nil
}

func decryptChunk(key []byte, index int, chat string, a actor, operation string, wire []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil || len(wire) < gcm.NonceSize()+gcm.Overhead() {
		return nil, errors.New("invalid encrypted chunk")
	}
	return gcm.Open(nil, wire[:gcm.NonceSize()], wire[gcm.NonceSize():], cryptoenc.Binding(chat, a.UserID, a.DeviceID, operation+"/"+strconv.Itoa(index)))
}

func projection(m core.Message, p payload, in contextInput, b *Bridge) Message {
	sender := m.Sender
	if m.Sender == b.actor(in.ProcessID, "owner", in.OwnerID).UserID {
		sender = in.OwnerID
	}
	if m.Sender == b.actor(in.ProcessID, "candidate", in.CandidateID).UserID {
		sender = in.CandidateID
	}
	files := p.Files
	if files == nil {
		files = []File{}
	}
	return Message{ID: m.ID, Sequence: m.Seq, SenderID: sender, CreatedAt: time.Unix(m.CreatedAt, 0).UTC().Format(time.RFC3339), Text: p.Text, Files: files}
}
func fileIDs(files []File) []string {
	out := make([]string, len(files))
	for i, f := range files {
		out[i] = f.ID
	}
	return out
}
func validContext(in contextInput) bool {
	return validID(in.ProcessID) && validID(in.OwnerID) && validID(in.CandidateID) && validID(in.ActorID) && in.OwnerID != in.CandidateID
}
func validID(v string) bool {
	if v == "" || len(v) > 128 {
		return false
	}
	for _, c := range v {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}
func validMetadata(name, media string) bool {
	if name == "" || len(name) > 255 || media == "" || len(media) > 127 || strings.Count(media, "/") != 1 {
		return false
	}
	for _, c := range name {
		if c <= 0x1f || c == '/' || c == '\\' {
			return false
		}
	}
	for _, c := range media {
		if c == '/' {
			continue
		}
		if !strings.ContainsRune("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$&^_.+-", c) {
			return false
		}
	}
	return true
}
func stableID(prefix string, values ...string) string {
	h := sha256.New()
	for _, v := range values {
		_, _ = h.Write([]byte(v))
		_, _ = h.Write([]byte{0})
	}
	return prefix + "-" + hex.EncodeToString(h.Sum(nil))[:48]
}
func hmacBytes(key []byte, text string) []byte {
	h := hmac.New(sha256.New, key)
	_, _ = h.Write([]byte(text))
	return h.Sum(nil)
}
func devicePublic(seed []byte) []byte {
	key, _ := ecdh.X25519().NewPrivateKey(seed)
	return key.PublicKey().Bytes()
}
func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	aa, bb := append([]string(nil), a...), append([]string(nil), b...)
	for i := range aa {
		for j := i + 1; j < len(aa); j++ {
			if aa[j] < aa[i] {
				aa[i], aa[j] = aa[j], aa[i]
			}
			if bb[j] < bb[i] {
				bb[i], bb[j] = bb[j], bb[i]
			}
		}
	}
	return bytes.Equal([]byte(strings.Join(aa, "\x00")), []byte(strings.Join(bb, "\x00")))
}

type apiErr struct {
	status  int
	message string
}

func (e apiErr) Error() string { return e.message }
func qErr(r qResponse) error {
	var x struct {
		Error string `json:"error"`
	}
	_ = json.Unmarshal(r.body, &x)
	if x.Error == "" {
		x.Error = "QGramm request failed"
	}
	return apiErr{r.status, x.Error}
}
func writeAPIError(w http.ResponseWriter, err error) {
	var e apiErr
	if errors.As(err, &e) {
		writeError(w, e.status, e.message)
		return
	}
	var q *core.APIError
	if errors.As(err, &q) {
		writeError(w, q.Status, q.Message)
		return
	}
	writeError(w, 502, "QGramm unavailable")
}
func writeQResponse(w http.ResponseWriter, r qResponse) { writeAPIError(w, qErr(r)) }
func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func decodeJSON(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		writeError(w, 400, "invalid JSON")
		return false
	}
	var trailing any
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeError(w, 400, "trailing JSON")
		return false
	}
	return true
}
func requiredSecret(name string, min int) ([]byte, error) {
	value := os.Getenv(name)
	if len(value) < min {
		return nil, fmt.Errorf("%s must contain at least %d characters", name, min)
	}
	return []byte(value), nil
}
func encodedSecret(name string, size int) ([]byte, error) {
	value := os.Getenv(name)
	raw, err := base64.StdEncoding.DecodeString(value)
	if err != nil || len(raw) != size {
		return nil, fmt.Errorf("%s must be base64 encoded %d-byte secret", name, size)
	}
	return raw, nil
}

func run() error {
	path := os.Getenv("CHAT_QGRAMM_CONFIG")
	if path == "" {
		path = "/etc/comit/qgramm.toml"
	}
	b, err := New(path)
	if err != nil {
		return err
	}
	defer b.Close()
	listen := os.Getenv("CHAT_BRIDGE_LISTEN")
	if listen == "" {
		listen = "127.0.0.1:8080"
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Addr: listen, Handler: b.Handler(), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 20 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: time.Minute}
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if server.Shutdown(shutdown) != nil {
			_ = server.Close()
		}
	}()
	err = server.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		<-shutdownDone
		return nil
	}
	return err
}

func main() {
	if run() != nil {
		fmt.Fprintln(os.Stderr, "chat_startup_or_listener_failed")
		os.Exit(1)
	}
}
