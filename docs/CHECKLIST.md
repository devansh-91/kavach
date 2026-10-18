# Kavach — verification checklist

Checked on 18 Oct 2026 against the IBM Z Datathon submission form, the product spec and a
payments-grade security review. ✅ verified · ⏳ pending · ⚠️ known limitation (documented).

## A. Submission requirements (Hack2Skill form)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| A1 | Challenge fits a theme | ✅ | Real-Time AI for Critical Decisions ("Detect. Decide. Respond.") |
| A2 | Problem statement ≤ 1024 chars | ✅ | 780 chars (`docs/submission.md`) |
| A3 | Solution overview ≤ 1024 chars | ✅ | 996 chars |
| A4 | IBM Z / LinuxONE answer | ✅ | 918 chars |
| A5 | Pitch deck PDF ≤ 5 MB, readable | ✅ | 11 pages, 0.53 MB, selectable text, not encrypted |
| A6 | GitHub link public, https, no login needed | ✅ | repo, raw README and raw PDF all HTTP 200 anonymously |
| A7 | Video ≤ 3 min on YouTube/Vimeo | ⏳ | script ready in `docs/submission.md`; recording by team |
| A8 | Open-source resources ≤ 1024 chars | ✅ | 450 chars |
| A9 | Event code SSF2026 in project description | ✅ | README, deck, form answers |
| A10 | Solution actually runs on IBM Z / LinuxONE | ✅ | LinuxONE Community Cloud VM on an **IBM z17** (machine 9175, `nnpa` present): trained, 15/15 tests, full replay and dashboard served from it (`artifacts/linuxone/`) |
| A11 | Architecture diagram | ✅ | README (Mermaid) + deck slide 5 |
| A12 | Numbers identical across README, deck, form and `artifacts/linuxone/meta.json` | ✅ | PR-AUC 0.992; 28.2 / 79.6 / 97.3 %; 33,538 txns, 554 frauds |

## B. Product functionality

| # | Check | Status | Evidence |
|---|---|---|---|
| B1 | Every payment scored in-line → ALLOW / STEP-UP / HOLD | ✅ | live replay of 33,538 txns, 0 server errors |
| B2 | Human-readable reasons, only on the risky side | ✅ | what-if attribution; age reasons only for accounts < 90 days |
| B3 | Mule-flow features + watchlist | ✅ | `test_mule_pass_through_is_visible` |
| B4 | Hinglish scam-call detection feeds payment risk | ✅ | `test_scam_call_raises_risk`; short or empty calls ignored |
| B5 | Held-out accuracy (LinuxONE) | ✅ | 97.3 % recall at 0.1 % FPR vs 79.6 % per-txn ML vs 28.2 % rule (x86 run: 96.2 / 82.1 %) |
| B6 | Live replay outcome (LinuxONE) | ✅ | 98.4 % fraud stopped, 96.2 % of victim money protected, 0.046 % legit held, 0.107 % legit OTP |
| B7 | Latency on z17 | ✅ | CPU trees: p50 28 µs, p99 52 µs, ~285k txn/s (batch 64) |
| B8 | Telum AI accelerator (zDNN) on z17 | ✅ | needed `libzdnn0` (now in setup); bit-identical predictions, 100 % decision agreement; p50 49 µs, slower than the cores for this small model, which the docs state honestly |
| B9 | No training/serving skew, no leakage | ✅ | one streaming feature code path; time split; `test_features_only_use_the_past` |
| B10 | Reproducible | ✅ | identical metrics on the laptop and in a clean Ubuntu 24.04 container |
| B11 | Dashboard works (desktop + 390 px phone, no console errors) | ✅ | Playwright on the laptop and through an SSH tunnel to LinuxONE; feed freezes on hover so rows are clickable |
| B11b | WebSocket on a minimal install | ✅ | bug found on LinuxONE (plain `uvicorn` has no WebSocket support → 404); fixed by pinning `websockets`, plus a regression test |
| B12 | Amount-splitting by a fresh account with no call and no mule signal | ⚠️ | 6 × ₹4,999 to a new person is allowed; a cumulative new-payee limit is the next policy |
| B12b | Paying the same person again right after a scam call | ⚠️ | the scam-call guardrail covers only *new* payees, so a second payment to someone first paid minutes earlier can be allowed when the model score is low (seen for 1 of 3 test users); next policy: treat payees first paid in the last 24 h as new. Demo users U00344 → U00270 never appear in the replay, so the demo is repeatable |
| B13 | Synthetic data; call model trained on templates | ⚠️ | stated in README and deck |
| B14 | Replay runs once | ⚠️ | restart the server for a fresh demo (~3 s) |

## C. Security (payments-grade)

| # | Control | Status | Evidence |
|---|---|---|---|
| C1 | No secrets / keys in repo or full git history | ✅ | pattern scan of `git log -p --all`; no `.pem`, `.pub`, `.env` tracked; private key mode 600 |
| C2 | Commit author email is public | ⚠️ | normal for GitHub; can switch to a noreply address |
| C3 | Dependencies pinned and free of known CVEs | ✅ | `requirements.txt` + setup script pinned (incl. `websockets`); `pip-audit`: 0 known vulnerabilities |
| C4 | Static analysis | ✅ | `bandit`: 0 high, 0 medium (2 low = simulation RNG, not security-relevant) |
| C5 | No unsafe deserialisation (CWE-502) | ✅ | pickle removed; CSV data; call model rebuilt deterministically |
| C6 | Model integrity | ✅ | SHA-256 pinned in `meta.json`; tampered model refused (`test_tampered_model_is_refused`) |
| C7 | Fail-safe, not fail-open | ✅ | engine error → STEP-UP (`test_fail_safe_never_allows_on_error`) |
| C8 | Authentication | ✅ | `KAVACH_API_KEY`, constant-time compare; WebSocket key as first message, never in URLs; dashboard wipes `#key=` from the address bar on load |
| C9 | Input validation | ✅ | NaN/Infinity, > ₹10 crore, ≤ 0, script tags, unknown fields, oversize IDs, self-payments → 422 |
| C10 | Rate limiting / DoS | ✅ | per-IP token bucket → 429 (`test_rate_limit`); max 50 sockets; slow sockets dropped |
| C11 | XSS | ✅ | all dynamic text escaped; CSP blocks inline script; `<img onerror>` payload did not run in the browser |
| C12 | Clickjacking | ✅ | `X-Frame-Options: DENY` + `frame-ancestors 'none'` |
| C13 | Cross-site API calls (CSRF/CORS) | ✅ | no CORS headers → preflight from other origins fails; JSON-only API |
| C14 | API surface disclosure | ✅ | `/docs` and `/openapi.json` off unless `KAVACH_DOCS=1` |
| C15 | Third-party script integrity | ✅ | SRI sha384 on Cytoscape from cdnjs |
| C16 | Audit trail | ✅ | `artifacts/audit.jsonl`, append-only, file mode 600, model hash per record |
| C17 | Framing attack on innocent accounts | ✅ | fixed: watchlist needs ≥ 2 senders, expires in 72 h, and only adds an OTP to the suspect's own outflows |
| C18 | Account takeover (new device, night, new payee) | ✅ | first ₹25k drain → STEP-UP, rest HOLD (`test_new_device_drain_is_stepped_up`) |
| C19 | Network exposure | ✅ | on the VM the server listens on 127.0.0.1:8000 only (checked with `ss`); viewed through an SSH tunnel; API key file mode 600; SSH key-only |
| C20 | Privacy | ✅ | synthetic data; no external AI APIs; transcripts not logged |
| C20b | Availability | ✅ | on the VM the server runs as a systemd service (`Restart=always`, enabled at boot); killed with `kill -9` it was back in 8 s; the laptop tunnel reconnects by itself (tested: 7 s) |
| C21 | Production hardening beyond a hackathon | ⚠️ | still needs TLS/mTLS, a secrets manager/HSM, an analyst review queue, DPDP-compliant retention, and a shared rate limiter for multiple instances |

**Test suite:** `python -m pytest -q tests` → 15 passed on the laptop and on LinuxONE z17 (14/14 in a clean Ubuntu 24.04 container before the WebSocket test was added).
