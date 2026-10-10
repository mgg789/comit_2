// Participant unread metadata reuses QGramm's durable monotonic read receipts.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
)

type readInput struct {
	contextInput
	ThroughSequence int64 `json:"through_sequence"`
}

type readStatus struct {
	ReadSequence int64 `json:"read_sequence"`
	UnreadCount  int64 `json:"unread_count"`
}

type unreadItem struct {
	ProcessID string `json:"process_id"`
	readStatus
}

// unreadStatus counts only live incoming messages without decrypting contents or
// provisioning untouched chats. The private caller already owns the Comit grant.
func (b *Bridge) unreadStatus(ctx context.Context, in contextInput) (readStatus, error) {
	if !validContext(in) {
		return readStatus{}, apiErr{400, "invalid chat context"}
	}
	if in.ActorID != in.OwnerID && in.ActorID != in.CandidateID {
		return readStatus{}, apiErr{403, "actor is not a chat member"}
	}
	role := "owner"
	if in.ActorID == in.CandidateID {
		role = "candidate"
	}
	a := b.actor(in.ProcessID, role, in.ActorID)
	chat := stableID("chat", in.ProcessID, in.OwnerID, in.CandidateID)
	var exists, purged bool
	if err := b.core.DB.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM chats WHERE id=?), EXISTS(SELECT 1 FROM comit_purged_chats WHERE chat_id=?)`, chat, chat).Scan(&exists, &purged); err != nil {
		return readStatus{}, err
	}
	if purged {
		return readStatus{}, apiErr{404, "chat not found"}
	}
	if !exists {
		return readStatus{}, nil
	}
	if _, _, _, err := b.core.Member(ctx, a.UserID, chat); err != nil {
		return readStatus{}, err
	}
	var active bool
	if err := b.core.DB.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM devices d JOIN users u ON u.id=d.user_id WHERE d.id=? AND d.user_id=? AND d.revoked=0 AND u.disabled=0)`, a.DeviceID, a.UserID).Scan(&active); err != nil {
		return readStatus{}, err
	}
	if !active {
		return readStatus{}, apiErr{403, "chat device unavailable"}
	}
	var status readStatus
	err := b.core.DB.QueryRowContext(ctx, `SELECT COALESCE((SELECT read FROM cursors WHERE device_id=? AND chat_id=?),0),
        (SELECT COUNT(*) FROM messages WHERE chat_id=? AND sender<>? AND deleted=0 AND seq>COALESCE((SELECT read FROM cursors WHERE device_id=? AND chat_id=?),0))`,
		a.DeviceID, chat, chat, a.UserID, a.DeviceID, chat).Scan(&status.ReadSequence, &status.UnreadCount)
	return status, err
}

// handleRead delegates range validation, monotonicity and persistence to the
// existing authenticated QGramm receipt route, then returns current metadata.
func (b *Bridge) handleRead(w http.ResponseWriter, r *http.Request) {
	var in readInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if in.ThroughSequence < 0 || in.ThroughSequence > 9007199254740991 {
		writeError(w, 400, "invalid read cursor")
		return
	}
	a, chat, err := b.context(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	// Read acknowledgement must refer to a returned message, never a future or
	// unrelated control-journal sequence. Zero is the empty-history cursor.
	if in.ThroughSequence > 0 {
		var sequence int64
		if err := b.core.DB.QueryRowContext(r.Context(), `SELECT seq FROM messages WHERE chat_id=? AND seq=? AND deleted=0`, chat, in.ThroughSequence).Scan(&sequence); err != nil {
			if err == sql.ErrNoRows {
				writeError(w, 400, "read cursor is not a live message")
				return
			}
			writeAPIError(w, err)
			return
		}
	}
	body, _ := json.Marshal(map[string]int64{"delivered": in.ThroughSequence, "read": in.ThroughSequence})
	res, err := b.qCall(r.Context(), a, http.MethodPost, "/v1/chats/"+chat+"/receipts", body, "application/json")
	if err != nil {
		writeAPIError(w, err)
		return
	}
	if res.status != 200 {
		writeQResponse(w, res)
		return
	}
	status, err := b.unreadStatus(r.Context(), in.contextInput)
	if err != nil {
		writeAPIError(w, err)
		return
	}
	writeJSON(w, 200, status)
}

// handleUnread bounds a participant-scoped status batch to one recruitment page.
// Its response contains counters only, never messages, files or private contacts.
func (b *Bridge) handleUnread(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Chats []contextInput `json:"chats"`
	}
	if !decodeJSON(w, r, &in) {
		return
	}
	if len(in.Chats) < 1 || len(in.Chats) > 100 {
		writeError(w, 400, "invalid unread batch")
		return
	}
	items := make([]unreadItem, 0, len(in.Chats))
	seen := make(map[string]bool)
	for _, chat := range in.Chats {
		if seen[chat.ProcessID] {
			writeError(w, 400, "duplicate process")
			return
		}
		seen[chat.ProcessID] = true
		status, err := b.unreadStatus(r.Context(), chat)
		if err != nil {
			writeAPIError(w, err)
			return
		}
		items = append(items, unreadItem{ProcessID: chat.ProcessID, readStatus: status})
	}
	writeJSON(w, 200, map[string]any{"items": items})
}
