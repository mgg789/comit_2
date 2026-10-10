CREATE TABLE api_rate_windows (
  key_hash text NOT NULL CHECK (length(key_hash)=64),
  window_start bigint NOT NULL,
  request_count integer NOT NULL CHECK (request_count > 0),
  PRIMARY KEY(key_hash,window_start)
);
CREATE INDEX api_rate_windows_expiry ON api_rate_windows(window_start);
