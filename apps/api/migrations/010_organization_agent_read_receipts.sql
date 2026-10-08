-- Freeze read snapshots under the same owner/key replay boundary as writes.
ALTER TABLE organization_agent_tool_receipts
 DROP CONSTRAINT organization_agent_tool_receipts_tool_check;
ALTER TABLE organization_agent_tool_receipts ADD CONSTRAINT organization_agent_tool_receipts_tool_check
 CHECK(tool IN ('organization_stats','vacancy_stats','recruitment_list','recruitment_get',
 'candidate_showcase_search','candidate_showcase_detail','candidate_context_search','candidate_context_detail',
 'candidate_feed','candidate_feed_continue','feed_decision','feed_impression',
 'candidate_chat_read','candidate_chat_send','interview_propose'));
