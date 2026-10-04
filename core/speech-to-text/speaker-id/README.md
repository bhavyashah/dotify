# Speaker identification (named speakers)

Turns conversation mode's anonymous `A:` / `B:` prefixes into real names:
enroll a person's voice once (~20 s), and their turns print as `Alice:`.
Rides on AssemblyAI conversation mode (the single opt-in for everything
speaker-related — labels only exist there). Everything runs locally on CPU —
no cloud, no audio ever stored, voiceprints live in the gitignored
`speakers/` folder at the repo root.

## Setup (one time)

```
cd core/speech-to-text/speaker-id
pip install -r requirements.txt      # numpy + onnxruntime only (no torch)
python download_model.py             # fetches the WeSpeaker ResNet34 ONNX (26.5 MB)
```

## Run

```
python server.py                     # listens on 127.0.0.1:8792
```

Start it alongside `node server.js`. If it isn't running, nothing breaks —
the speech server logs one line and sessions fall back to plain `A:` / `B:` labels.

Enrollment: open the transcription page, find "Named speakers", type the
person's name, press "Enroll voice", and have them speak for 20 seconds.
Enrollment needs at least 8 s of clear speech or it asks you to retry.

## How identification works

1. The AssemblyAI provider buffers each turn's PCM and hands it to the speech
   server with the turn's diarization label.
2. The speech server fires it (fire-and-forget, never delaying text) at this
   service's `/identify?label=A`.
3. The service trims silence, resamples 24 kHz -> 16 kHz, embeds with the
   ONNX model, and folds the embedding into a speech-seconds-weighted
   centroid for that label.
4. Once a label has >= 3 s of accumulated speech AND its best cosine score
   against the enrolled voiceprints clears the threshold (0.40) with a 0.06
   margin over the runner-up, the label is assigned that name — sticky for
   the session, and no name is ever given to two labels.
5. The speech server prints names only at prefix time, so a name learned
   mid-conversation upgrades future prefixes; if the current speaker's name
   just resolved, one extra prefix is printed so the reader learns it now.

Single turns are deliberately not trusted: a 2-second clip of even a strong
model is noisy (EER roughly doubles under 2 s), but a few turns averaged into
a centroid are stable. Wrong names on a braille display are worse than
anonymous labels, so every constant here errs conservative.

## Model

WeSpeaker ResNet34 large-margin (VoxCeleb2-trained, Apache-2.0, 26.5 MB,
256-dim embeddings, ~0.72% EER on VoxCeleb1-O, ~50-130 ms per turn on one
CPU thread). Verified against this repo's frontend: the kaldi-fbank
implementation in `fbank.py` matches `torchaudio.compliance.kaldi.fbank`
to < 2e-3 (see `tests/test_fbank.py`).

Do not swap in WeSpeaker's CAM++ ONNX export without testing: it produced
length-unstable embeddings (nested prefixes of the same audio embed
near-orthogonally at certain lengths). The probe: embed `clip[:4s]`,
`clip[:5s]`, `clip[:6s]` and check that pairwise cosines are all > 0.9.

## Tuning (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DOTIFY_SPEAKER_ID_PORT` | 8792 | Service port |
| `DOTIFY_SPEAKERS_DIR` | `<repo>/speakers` | Voiceprint JSON folder |
| `DOTIFY_SPEAKER_THRESHOLD` | 0.40 | Min cosine score to assign a name |
| `DOTIFY_SPEAKER_MARGIN` | 0.06 | Required lead over the runner-up |
| `DOTIFY_SPEAKER_MIN_SPEECH` | 3.0 | Seconds of speech before deciding |
| `DOTIFY_SPEAKER_MIN_ENROLL` | 8.0 | Min clear speech for enrollment |
| `DOTIFY_SPEAKER_THREADS` | 2 | ONNX intra-op CPU threads |

Thresholds are model-specific — recalibrate if the model changes. Reference
numbers from the shipped model on real speech: same-speaker turns score
~0.65-0.82 against an enrolled voiceprint; different speakers ~0.01-0.13.

## Tests

```
python -m pytest -q
```

Covers kaldi-fbank invariants (+ exact torchaudio comparison when
torchaudio is installed), resampler frequency response, silence trimming,
voiceprint store round-trip, and the full matching decision table. The Node
side has its own suite (`node --test` in `core/speech-to-text`), including a
stubbed-service integration test of the naming flow.
