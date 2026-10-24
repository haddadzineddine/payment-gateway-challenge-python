from prometheus_client import Counter, Histogram

PAYMENTS = Counter("payments", "New payments by status (Rejected = invalid input)", ["status"])

IDEMPOTENT_REPLAYS = Counter("idempotent_replays", "Requests answered from an Idempotency-Key, bank not called")

BANK_REQUEST_DURATION = Histogram(
    "bank_request_duration_seconds",
    "Calls to the acquiring bank, by outcome (authorized, declined, timeout, not_sent, ...)",
    ["outcome"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),  # the bank timeout is 5 s by default
)
