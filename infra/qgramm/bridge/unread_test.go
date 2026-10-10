package main

import (
	"github.com/google/uuid"
	"net/http/httptest"
	"testing"
)

func TestUnreadReceiptsAreIncomingMonotonicAndDurable(t *testing.T) {
	ts := newTestServer(t)
	token := string(ts.b.serviceToken)
	candidate := ts.c
	candidate.ActorID = candidate.CandidateID
	status := func(in contextInput) readStatus {
		t.Helper()
		var result struct {
			Items []unreadItem `json:"items"`
		}
		if got := ts.call(t, "/internal/chat/unread", map[string]any{"chats": []contextInput{in}}, token, &result); got != 200 || len(result.Items) != 1 {
			t.Fatalf("status %d: %+v", got, result)
		}
		return result.Items[0].readStatus
	}
	if got := status(candidate); got.UnreadCount != 0 {
		t.Fatalf("untouched: %+v", got)
	}
	var rooms int
	if err := ts.b.core.DB.QueryRow(`SELECT COUNT(*) FROM chats`).Scan(&rooms); err != nil || rooms != 0 {
		t.Fatalf("status provisioned a chat: %d %v", rooms, err)
	}
	send := func(in contextInput) Message {
		t.Helper()
		var message Message
		if got := ts.call(t, "/internal/chat/send", sendInput{contextInput: in, IdempotencyKey: uuid.NewString(), Text: "unread fixture"}, token, &message); got != 200 {
			t.Fatalf("send: %d", got)
		}
		return message
	}
	first := send(ts.c)
	second := send(ts.c)
	outgoing := send(candidate)
	if got := status(candidate); got.UnreadCount != 2 || got.ReadSequence != 0 {
		t.Fatalf("incoming count: %+v", got)
	}
	if got := status(ts.c); got.UnreadCount != 1 {
		t.Fatalf("owner count: %+v", got)
	}
	acknowledge := func(sequence int64) readStatus {
		t.Helper()
		var result readStatus
		if got := ts.call(t, "/internal/chat/read", readInput{contextInput: candidate, ThroughSequence: sequence}, token, &result); got != 200 {
			t.Fatalf("read: %d", got)
		}
		return result
	}
	if got := acknowledge(first.Sequence); got.ReadSequence != first.Sequence || got.UnreadCount != 1 {
		t.Fatalf("partial read: %+v", got)
	}
	if got := acknowledge(second.Sequence); got.UnreadCount != 0 {
		t.Fatalf("full read: %+v", got)
	}
	if got := acknowledge(first.Sequence); got.ReadSequence != second.Sequence || got.UnreadCount != 0 {
		t.Fatalf("older retry moved cursor: %+v", got)
	}
	if got := ts.call(t, "/internal/chat/read", readInput{contextInput: candidate, ThroughSequence: outgoing.Sequence + 1000}, token, nil); got != 400 {
		t.Fatalf("future cursor: %d", got)
	}
	// Receipts append a control event. That event cannot acknowledge unread text.
	var journal int64
	chat := stableID("chat", candidate.ProcessID, candidate.OwnerID, candidate.CandidateID)
	if err := ts.b.core.DB.QueryRow(`SELECT seq FROM chats WHERE id=?`, chat).Scan(&journal); err != nil {
		t.Fatal(err)
	}
	if got := ts.call(t, "/internal/chat/read", readInput{contextInput: candidate, ThroughSequence: journal}, token, nil); got != 400 {
		t.Fatalf("control cursor: %d", got)
	}
	third := send(ts.c)
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
	if got := status(candidate); got.ReadSequence != second.Sequence || got.UnreadCount != 1 {
		t.Fatalf("restart: %+v", got)
	}
	if got := acknowledge(third.Sequence); got.UnreadCount != 0 {
		t.Fatalf("latest: %+v", got)
	}
	if got := status(ts.c); got.UnreadCount != 1 {
		t.Fatalf("other participant was marked: %+v", got)
	}
	invalid := candidate
	invalid.ActorID = "foreign"
	if got := ts.call(t, "/internal/chat/read", readInput{contextInput: invalid, ThroughSequence: third.Sequence}, token, nil); got != 403 {
		t.Fatalf("foreign read: %d", got)
	}
	if got := ts.call(t, "/internal/chat/unread", map[string]any{"chats": []contextInput{invalid}}, token, nil); got != 403 {
		t.Fatalf("foreign count: %d", got)
	}
	if got := ts.call(t, "/internal/chat/unread", map[string]any{"chats": []contextInput{candidate}}, "wrong", nil); got != 401 {
		t.Fatalf("private token: %d", got)
	}
	if got := ts.call(t, "/internal/chat/purge", ts.c, token, nil); got != 200 {
		t.Fatalf("purge: %d", got)
	}
	if got := ts.call(t, "/internal/chat/unread", map[string]any{"chats": []contextInput{candidate}}, token, nil); got != 404 {
		t.Fatalf("purged counter recreated chat: %d", got)
	}
}
