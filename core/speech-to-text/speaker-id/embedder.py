# ONNX speaker-embedding inference. Model-agnostic: any WeSpeaker-style ONNX
# export works (input: float32 [batch, frames, 80] kaldi fbank with utterance
# mean subtracted; output: [batch, dim] embedding). Download a model with
# download_model.py; models/model.json records which file to load.

import json
import os
from pathlib import Path

import numpy as np

import audio
import fbank

MODELS_DIR = Path(os.environ.get(
    'DOTIFY_SPEAKER_MODEL_DIR', Path(__file__).resolve().parent / 'models'))

# Enrollment is embedded in overlapping windows and averaged — the standard
# multi-utterance enrollment trick; it is markedly more robust than one
# whole-clip embedding when the enrollment audio spans pauses and level shifts.
ENROLL_WINDOW_S = 3.0
ENROLL_HOP_S = 1.5


class ModelNotInstalled(Exception):
    pass


class Embedder:
    def __init__(self, models_dir=MODELS_DIR):
        manifest = Path(models_dir) / 'model.json'
        if not manifest.exists():
            raise ModelNotInstalled(
                f'No speaker model installed — run download_model.py '
                f'(expected {manifest}).')
        meta = json.loads(manifest.read_text(encoding='utf-8'))
        model_path = Path(models_dir) / meta['file']
        if not model_path.exists():
            raise ModelNotInstalled(f'Model file missing: {model_path}')

        import onnxruntime  # imported lazily so tests without it can run
        opts = onnxruntime.SessionOptions()
        opts.intra_op_num_threads = int(os.environ.get('DOTIFY_SPEAKER_THREADS', '2'))
        self.session = onnxruntime.InferenceSession(
            str(model_path), sess_options=opts, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.name = meta.get('name', model_path.stem)
        self.dim = meta.get('dim')

    def _run(self, feats):
        out = self.session.run(None, {self.input_name: feats[None].astype(np.float32)})
        emb = np.asarray(out[0], dtype=np.float64).reshape(-1)
        norm = np.linalg.norm(emb)
        return emb / norm if norm > 0 else emb

    def embed_16k(self, wave16k):
        """L2-normalized embedding of a 16 kHz int16-scale waveform."""
        feats = fbank.fbank(wave16k)
        if len(feats) < 25:  # < 0.25 s of frames: too short to embed
            return None
        feats = feats - feats.mean(axis=0, keepdims=True)
        return self._run(feats)

    def embed_turn(self, pcm24k_bytes):
        """One finalized turn: trim silence, resample, embed.
        Returns (embedding | None, net_speech_seconds)."""
        wave = audio.decode_pcm16(pcm24k_bytes)
        trimmed, speech_s = audio.trim_to_speech(wave)
        if speech_s < 0.5:
            return None, speech_s
        return self.embed_16k(audio.resample_to_16k(trimmed)), speech_s

    def embed_enrollment(self, pcm24k_bytes):
        """Enrollment clip: average of overlapping-window embeddings over the
        speech-trimmed audio. Returns (embedding | None, net_speech_seconds)."""
        wave = audio.decode_pcm16(pcm24k_bytes)
        trimmed, speech_s = audio.trim_to_speech(wave)
        if len(trimmed) == 0:
            return None, speech_s
        wave16 = audio.resample_to_16k(trimmed)

        window = int(ENROLL_WINDOW_S * audio.TARGET_RATE)
        hop = int(ENROLL_HOP_S * audio.TARGET_RATE)
        # A clip no longer than one window is embedded whole (one start, 0).
        starts = range(0, max(1, len(wave16) - window + 1), hop)
        embeddings = [emb for emb in
                      (self.embed_16k(wave16[s:s + window]) for s in starts)
                      if emb is not None]
        if not embeddings:
            return None, speech_s
        mean = np.mean(embeddings, axis=0)
        return mean / np.linalg.norm(mean), speech_s
