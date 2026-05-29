"""Lua scripts for atomic throttle operations against Redis.

Each script executes as a single ``EVALSHA`` round-trip so two workers
cannot race on the same counter under high concurrency.

Two scripts live here:

* :data:`THROTTLE_LUA_SCRIPT` — per-identifier sliding window backed by
  a sorted set (``ZREMRANGEBYSCORE`` + ``ZCARD`` + ``ZADD``). Sliding
  window precision; heavier ops. Used by ``RedisThrottle.check`` for
  per-(user|endpoint|ip|tier) buckets where exact sliding semantics
  matter (a burst at the edge of two fixed windows must not pass).
* :data:`GLOBAL_THROTTLE_LUA_SCRIPT` — O(1) sliding-window-counter
  approximation backed by two fixed-window keys with a weighted
  average. Three string ops (one ``GET`` of each key, one ``INCR``,
  one ``EXPIRE``). Cheaper than the sorted-set path; appropriate for
  *high-RPS global gates* (cluster-wide concurrency caps, outbound-
  call quotas) where the small precision loss at window boundaries
  is acceptable.

Both scripts return ``{allowed (0/1), count_or_effective_count, ttl}``
so the Python layer can produce a uniform :class:`ThrottleResult`.
"""

from __future__ import annotations

# Per-identifier sliding window via sorted set.
# Keys: 1  — the bucket key.
# Argv: limit, window_seconds, now (unix seconds).
# Returns: {allowed, current_count, ttl}.
THROTTLE_LUA_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)
local count = redis.call('ZCARD', key)

if count >= limit then
    local ttl = redis.call('TTL', key)
    if ttl < 0 then ttl = window end
    return {0, count, ttl}
end

redis.call('ZADD', key, now, tostring(now) .. ':' .. tostring(math.random(1000000)))
redis.call('EXPIRE', key, window)

return {1, count + 1, window}
"""


# Sliding window counter for cluster-wide global throttling (O(1)).
# Keys: 1  — a *prefix*; the script appends the window-start to form the
#            current + previous fixed-window keys.
# Argv: limit, window_seconds, now (unix seconds).
# Returns: {allowed, effective_count, ttl}.
#
# Semantics: instead of tracking every event in a sorted set, the script
# maintains a fixed-window counter per ``floor(now/window)``. The
# "effective count" mixes the current window's counter with the
# previous window's, weighted by how far through the current window we
# are. Approximates sliding semantics with one ``GET`` per neighbor +
# one ``INCR`` + one ``EXPIRE`` — much cheaper at high RPS than the
# sorted-set path.
GLOBAL_THROTTLE_LUA_SCRIPT = """
local key_prefix = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local window_start = math.floor(now / window) * window
local window_position = (now - window_start) / window

local current_key  = key_prefix .. ':' .. tostring(math.floor(window_start))
local previous_key = key_prefix .. ':' .. tostring(math.floor(window_start - window))

local current_count  = tonumber(redis.call('GET', current_key)  or '0')
local previous_count = tonumber(redis.call('GET', previous_key) or '0')

local effective_count = current_count + previous_count * (1 - window_position)

if effective_count >= limit then
    local ttl = window - (now - window_start)
    return {0, math.ceil(effective_count), math.ceil(ttl)}
end

redis.call('INCR', current_key)
redis.call('EXPIRE', current_key, window * 2)

return {1, math.ceil(effective_count) + 1, math.ceil(window - (now - window_start))}
"""


__all__ = ["GLOBAL_THROTTLE_LUA_SCRIPT", "THROTTLE_LUA_SCRIPT"]
