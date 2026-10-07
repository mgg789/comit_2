-- Cosine KNN uses the persisted 640-dimensional Harrier space.
CREATE INDEX candidate_pool_semantic_hnsw ON candidate_pipeline.pool
USING hnsw (semantic_vector vector_cosine_ops) WITH (m=16, ef_construction=128);
