# Dotify speech server

Captures microphone speech in the browser, transcribes it with a cloud
engine, and publishes the text for the braille engine
(`core/text-to-braille`) as a stream of revisable segments. Four keyed
engines are available; the Windows app adds an offline engine (a downloaded
Nemotron model) that feeds the same stream.

The desktop apps start this server for you. To run it by hand:

```
npm install
node server.js
```

Then open http://localhost:8788 in Chrome or Edge, add a key under
**Settings › API keys**, press **Start transcription**, and speak.

The server listens on `127.0.0.1:8788` only, and starts without any key: a
key is checked when a session for that engine starts, so a missing key is an
error on the page, not a failed boot.

## Engines

| Engine | Model | Key |
|---|---|---|
| `elevenlabs` (default) | Scribe v2 Realtime | `ELEVENLABS_API_KEY` (`11LABS_API_KEY` also accepted) |
| `assemblyai` | Universal-Streaming English | `ASSEMBLYAI_API_KEY` |
| `deepgram` | Nova-3 | `DEEPGRAM_API_KEY` |
| `openai` | gpt-live-transcribe | `OPENAI_API_KEY` |

ElevenLabs is the default because it had the lowest error rate on a
far-field meeting test (see [docs/LESSONS.md](../../docs/LESSONS.md)). AssemblyAI alone supports **conversation
mode** (experimental): finals are prefixed `A:` / `B:` when the speaker
changes, and with the optional speaker-id service
([speaker-id/README.md](speaker-id/README.md)) enrolled people's names
replace the letters. Because braille output is append-only, a suspected
speaker change is held until the next turn confirms it, so one-turn label
flickers never reach the display.

Each module under `providers/` hides one vendor behind the same
`createSession` interface, documented at the top of
`providers/openai-realtime.js`. To add an engine, write a module with that
shape, add it to `PROVIDERS` in `server.js`, and add an `<option>` to the
engine selector in `public/index.html`.

## Configuration

Keys saved in the page are written to `.env` beside `server.js` (or to
`DOTIFY_ENV_FILE`); you can also edit it directly, starting from
`.env.example`. The page never displays a saved key. The personal
dictionary (`dictionary.json`), session recordings (`recordings/`) and the
offline model (`models/`) live in the same folder as the `.env`.

Other settings, read from the environment (or `.env` for
`DOTIFY_SUMMARY_MODEL`):

| Variable | Default | Effect |
|---|---|---|
| `PORT` | 8788 | Listening port (the other components expect 8788) |
| `DOTIFY_ENV_FILE` | `./.env` | Key file; also anchors the user-data folder |
| `DOTIFY_TRANSCRIPT_FILE` | `./transcript.txt` | Finalized-text file |
| `DOTIFY_DICTIONARY_FILE`, `DOTIFY_MODELS_DIR` | beside the `.env` | Overrides for those paths |
| `DOTIFY_RECORD_DIR` | unset | Record every session into this folder |
| `DOTIFY_SUMMARY_MODEL` | `gpt-5.6-luna` | Model for `/summarize` |
| `DOTIFY_SPEAKER_ID_PORT` | 8792 | Speaker-id service port |
| `DOTIFY_COST_GATE` | on | `0` streams silence to the vendor too |
| `DOTIFY_SOFT_DISABLE` | off | `1` turns soft (early) text off |
| `DOTIFY_SOFT_REVISE_MS` | 300 | Minimum interval between revisions of one segment |
| `DOTIFY_SPEAKER_CONFIRM_MS` | 3000 | How long a suspected speaker change waits |
| `DOTIFY_INGEST_ABANDON_S` | 30 | Harden an `/ingest` segment whose sender went silent; `0` disables |
| `DOTIFY_SHOWN_ACK_MS` | 700 | How long a final waits for the display's ack |

The test suites use a few more (`DOTIFY_MOCK_PROVIDER`, `DOTIFY_MOCK_KEY`,
`DOTIFY_MOCK_DIE_FILE`, `DOTIFY_OPENAI_BASE_URL`, `DOTIFY_SPEAKER_ID_URL`).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | The transcription page |
| `WS /audio?provider=&last_seq=&conversation=&max_speakers=&record=&latency=` | Browser mic audio in (PCM16 mono 24 kHz binary frames), session events out |
| `WS /finalized` | The segment stream for consumers (below) |
| `POST /ingest` | Text from sessionless engines (below) |
| `POST /reset` | Start a fresh transcript (the page calls it on load) |
| `POST /summarize` | Jump-to-live summary (below) |
| `POST /latency` | `{mode}`: switch the latency preset (tests and tuning) |
| `GET /api/providers`, `POST /api/providers` | Which engines have keys; save or remove a key |
| `GET /api/dictionary`, `POST /api/dictionary` | The personal dictionary: `{add: {word, soundsLike?}}` or `{remove: word}` |
| `GET /api/offline-model`, `POST /api/offline-model` | Offline model status; `{download}`, `{cancel}` or `{remove}` |
| `GET /api/net-probe?engine=` | `{reachable}`: can the vendor be reached right now |
| `GET /api/speakers`, `POST`/`DELETE /api/speakers/<name>` | Speaker enrollment, proxied to the speaker-id service |

Settings writes (`POST /api/*`) must be JSON and carry the `X-Dotify-Token`
header from `GET /api/providers`, a per-boot token that a page from another
origin cannot read. Requests from a non-loopback `Origin` are refused.

## The `/finalized` stream

Text reaches the braille display early as **soft** segments, which are
corrected in place until they **harden**. Segment ids are unique per server
boot.

- `{"type":"soft","id","text","ts"}`: a new revisable segment, the stable
  prefix of the engine's current hypothesis.
- `{"type":"revise","revise":"<id>","text","ts"}`: new text for a soft
  segment; `""` withdraws it.
- `{"type":"final","id","text","speaker"?,"ts"}`: a hardened segment, never
  revised again. An id seen earlier as soft replaces that segment's text;
  a new id is a new segment.
- `{"type":"latency","mode","render","ts"}`: sent on connect and on change.
  `render` is `eager` (soft text may be shown, and is frozen once shown) or
  `confirmed` (show only hardened text).
- Upstream, consumer to server: `{"type":"shown","id","text"}` acknowledges
  a final with the text the display actually showed. In the `fastest`
  preset (which ElevenLabs sessions use) the transcript waits for that ack,
  so the screen matches what the reader felt.

A consumer that handles only `final` gets finalized text and nothing else;
see `consumer-example.js`. Every final is also appended to
`transcript.txt`; soft text never is.

Soft text is held back from the volatile tail of each hypothesis: either the
last two words, or, for engines with a measured filter (`SOFT_STABILITY` in
`server.js`), whatever has not stayed unchanged long enough. Conversation
mode emits no soft text, since speaker labels are decided at final time.

## `POST /ingest`

Sessionless engines (the Windows offline engine, the demo feeder, text typed
to the display) post JSON, at most 1,000,000 bytes:

- `{text, speaker?}`: a final. Answers `200` with `{text}` (as displayed,
  after dictionary correction), `{dropped:true}`, or `{deferred:true}` when
  it is waiting on a speaker hold or a display ack.
- `{interim:true, client, segment, text, engine?}`: the live hypothesis of an
  open segment (`client` is a per-page-load token, `segment` the utterance
  counter); `text:""` withdraws it, and a later final with the same
  `client`/`segment` hardens it. `engine` names a measured stability filter
  (the Windows app sends `nemotron`). Answers `204`.
- `{typed:true, text}`: text the user typed straight to the display;
  recorded in `transcript.txt` only. Answers `204`.

## `POST /summarize`

`{text, chars (4-2000), grade (1|2)}` returns `{summary, source}`: the missed
speech compressed into at most `chars` characters for the braille ticker's
jump-to-live. With an OpenAI key, a small model writes it; structured output
enforces the length limit. Without a key, or if the call fails, a local
extractive summary (the backlog's most frequent content words, in spoken
order) answers instead, with `source: "local"`.

## Language

The app is English only: every session pins `language: 'en'` (`LANGUAGE` in
`server.js`). For OpenAI, finals containing CJK or Hangul script are
dropped, since Whisper-family models hallucinate them on silence. ElevenLabs
can still transcribe real non-English speech; those words pass through.

## Latency and segmentation

Finals appear when an engine commits a segment. Vendor endpointing alone
would let continuous speech run unfinalized, so a shared gap gate
(`providers/speech-gate.js`) forces a boundary at the next short pause once
a segment passes ~1.5 s, or at ~2.5 s regardless. ElevenLabs is the
exception: forced boundaries hurt its accuracy, so it relies on its own
voice detection and reaches the reader through soft text instead.

A cost gate (`providers/cost-gate.js`) withholds silence from the vendors
and closes long-idle sessions, re-dialing on the next speech.

## Tools

- `tools/demo-feeder.js` replays a bundled caption track
  (`core/text-to-braille/demo_tracks/`) into `/ingest` at its real cue
  timing, for demos with nobody speaking:
  `node tools/demo-feeder.js --list-tracks`,
  `node tools/demo-feeder.js --track nasa-twan-2021-10-16 [--loop]`.
- `tools/wer-replay.js` replays a recorded session through the engines and
  scores each against a corrected reference. Record sessions with
  `DOTIFY_RECORD_DIR`, or with the page's developer option (open the page
  with `?dev=1`).
- `tools/stability-analysis.js`, `tools/nemotron-replay.js` and
  `tools/cost-gate-sweep.js` produced the numbers behind the stability
  filters and the cost gate.

## Tests

```
node --test --test-concurrency=1
```

The suites start servers on fixed ports, so run them one at a time. The
browser tests use Playwright (a dev dependency) and its Chromium
(`npx playwright install chromium`); they skip when Playwright is not
installed. The speaker-id service has its own Python tests.
