# Sprint 2 signal update contract

Ad's Sprint 2 code validates synthetic source records, converts them into the shared
`Signal` model, and preserves directional impacts and event timing in an Ad-owned
`NormalizedSignal` envelope. The fixture labels are explicit: this deterministic
fallback does not infer topics, confidence, sensitive attributes, or demand direction.

`propose_profile_update` rejects region mismatches, low-confidence, stale,
future-dated, short-evidence, blocked-topic, and duplicate-source inputs. It limits
the number of changed topics and clamps every change and event boost to the active
profile's `max_weight_delta`. It returns a candidate only; it never persists,
activates, or mutates the active profile. Z's repository layer can persist the
candidate and S's workflow adapter can attach integration evidence in later merges.

Event intervals are half-open (`starts_at <= now < expires_at`). An already-expired
event is rejected as stale, and the existing ranker ignores a boost at its expiry.
