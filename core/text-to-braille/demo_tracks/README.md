# Demo caption tracks

Timed caption tracks from real, public-domain U.S. government videos. They
exist so Dotify can be demonstrated, hand-tested, and tuned against real
caption cadence — bursts, pauses, and all — with no microphone, no speaker,
and no transcription key.

## Playing a track

**Through the speech server (the full live path).** With Dotify running,
the demo feeder replays a track into the local speech server at the cues'
own wall-clock offsets. Each cue is posted to `POST /ingest` as one final,
so it travels the same `/finalized` stream live captions do and reaches the
ticker exactly as a captioned talk would:

```bash
cd core/speech-to-text
node tools/demo-feeder.js --list-tracks
node tools/demo-feeder.js --track nasa-twan-2021-10-16
node tools/demo-feeder.js --track usgs-yellowstone-2014-03-26 --loop
```

`--track` takes a bundled name (with or without `.srt`) or a path to any
`.srt` file; `--rate` speeds the replay up for testing. See the speech
server's README for the full option list.

**Straight into the braille engine.** The Windows panel's caption-file
picker accepts any `.srt` or `.vtt`, including these. The engine's own
timed replay (`braille_engine/demo.py`, parsed by
`braille_engine/captions.py`) feeds the cues in-process, with no speech
server involved — Space+T stops it like the preset demo text.

## Catalog

### Live sports — `dma-basketball-tourney-2013.srt`

- Footage: *Air Force Report: Basketball Tourney*, a USA–Italy game at an
  international military tournament, 61 seconds.
- Source: <https://www.dvidshub.net/video/311103/air-force-report-basketball-tourney>
- Credit/license: Defense Media Activity – Air Force; marked public domain by
  DVIDS, subject to the DVIDS copyright notice.
- Captions: transcribed from the video's audio with Deepgram Nova-3 on
  2026-08-17, split from word timestamps into 21 live-sized cues, then
  reviewed for names, capitalization, and punctuation. Last cue ends at
  54.535 seconds.

### NASA — `nasa-twan-2021-10-16.srt`

- Footage: *The First Mission to the Trojan Asteroids on This Week @NASA
  – October 16, 2021*, NASA Image and Video Library asset of the same name.
- Source: <https://images.nasa.gov/details/The%20First%20Mission%20to%20the%20Trojan%20Asteroids%20on%20This%20Week%20@NASA%20%E2%80%93%20October%2016,%202021>
- Credit/license: NASA; generally public domain as a U.S. federal-government
  work. NASA acknowledgement does not imply endorsement.
- Captions: NASA's published SRT, 52 cues, last cue at 184.920 seconds.

### Weather preparedness — `afn-hurricane-preparedness-2024.srt`

- Footage: AFN field report on the American Red Cross hurricane
  preparedness event at Naval Station Guantanamo Bay, 60 seconds.
- Source: <https://www.dvidshub.net/video/930030/navsta-guantanamo-bay-hurricane-preparedness-event>
- Credit/license: U.S. Navy / AFN Guantanamo Bay; marked public domain by
  DVIDS, subject to the DVIDS copyright notice.
- Captions: transcribed from the video's audio with Deepgram Nova-3 on
  2026-08-17 and reviewed, 17 cues, last cue at 56.065 seconds.

### Safety board — `dma-safety-board-2016.srt`

- Footage: *Air Force Report: Safety Board*, covering the safety-board
  president course at Yokota Air Base, 59 seconds.
- Source: <https://www.dvidshub.net/video/451057/air-force-report-safety-board>
- Credit/license: Defense Media Activity – Air Force; marked public domain by
  DVIDS, subject to the DVIDS copyright notice.
- Captions: transcribed from the video's audio with Deepgram Nova-3 on
  2026-08-17 and reviewed, 21 cues, last cue at 57.675 seconds.

### Yellowstone science — `usgs-yellowstone-2014-03-26.srt`

- Footage: *Yes! Yellowstone is a Volcano*, an interview with USGS
  scientist Jake Lowenstern, 7 minutes 38 seconds.
- Source: <https://www.usgs.gov/media/videos/yes-yellowstone-volcano-part-1-3>
- Credit/license: U.S. Geological Survey; USGS-produced information is public
  domain. USGS acknowledgement does not imply endorsement.
- Captions: USGS's published English SRT, 131 cues, last cue at 455.190
  seconds.

## Generating captions

`core/speech-to-text/tools/deepgram-json-to-srt.py` converts a Deepgram
response's word timings into short cues, breaking at sentence punctuation,
speech gaps, or a ten-word ceiling. Raw API responses are kept out of the
repository. Generated text still needs human review for proper nouns and
overlapping speech; the three generated tracks above received that review.
