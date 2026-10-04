# Security and privacy

## What leaves your computer

- **Offline engine:** nothing. Audio is decoded on your machine. The only
  network access is the one-time model download from Hugging Face, which
  you start yourself.
- **Cloud engines (ElevenLabs, Deepgram, AssemblyAI, OpenAI):** microphone
  audio streams directly from your computer to the provider you picked,
  authenticated with your own API key. Silence is withheld (the cost gate),
  so the provider receives speech, not the whole room.
- **Summaries ("what did I miss?"):** with an `OPENAI_API_KEY` set, the
  missed text is sent to OpenAI to be summarized. Without one, a local
  extractive summary is used and nothing is sent.
- Dotify has no server of its own, no accounts, and no telemetry.

## What stays on disk

In the app's data folder (`%LOCALAPPDATA%\Dotify` on Windows):

- `.env` — your provider API keys, in plain text, readable only by your
  user account. Treat it like a password file.
- `dictionary.json` — your personal dictionary.
- `transcript.txt` — the current session's transcript, cleared when a new
  session starts.
- `recordings/` — only if you turn on session recording.
- `models/` — the offline speech model.

## Network exposure

Every Dotify service binds to `127.0.0.1` only. The speech server rejects
WebSocket and state-changing requests from non-loopback browser origins, so
a web page open in your browser can't read the live transcript or inject
text. Don't put these ports behind a tunnel or reverse proxy: they are
designed for one trusted local user.

## Reporting a vulnerability

Please report security problems privately through the repository's
**Security** tab (**Report a vulnerability**) rather than in a public issue.
