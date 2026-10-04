# Voiceprint store + per-session diarization-label -> name matching.
#
# Voiceprints live as one JSON file per speaker in the gitignored speakers/
# folder (personal data, never committed — repo rule). Only the embedding is
# stored, never audio.
#
# Matching (see README): per diarization label ("A", "B", ...) accumulate a
# speech-seconds-weighted centroid of turn embeddings. A label gets a name only
# once there is enough speech, the best cosine score clears a threshold, and
# it leads the runner-up by a margin: single short turns are unreliable, a few
# averaged turns are stable. An assigned name is sticky for the session; the
# braille stream is append-only, so silence beats flip-flopping.

import json
import os
import re
import time
from pathlib import Path

import numpy as np

DEFAULT_SPEAKERS_DIR = Path(__file__).resolve().parents[3] / 'speakers'
SPEAKERS_DIR = Path(os.environ.get('DOTIFY_SPEAKERS_DIR', DEFAULT_SPEAKERS_DIR))

# Cosine thresholds are model-specific and must be re-calibrated if the model
# changes (research consensus; see README). Defaults are for WeSpeaker
# VoxCeleb models and deliberately conservative: a wrong name on the braille
# display is worse than a bare "A:".
THRESHOLD = float(os.environ.get('DOTIFY_SPEAKER_THRESHOLD', '0.40'))
MARGIN = float(os.environ.get('DOTIFY_SPEAKER_MARGIN', '0.06'))
MIN_MATCH_SPEECH_S = float(os.environ.get('DOTIFY_SPEAKER_MIN_SPEECH', '3.0'))
MIN_ENROLL_SPEECH_S = float(os.environ.get('DOTIFY_SPEAKER_MIN_ENROLL', '8.0'))

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _'\-]{0,39}$")


def valid_name(name):
    return bool(_NAME_RE.match(name or ''))


def _slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


class VoiceprintStore:
    def __init__(self, directory=SPEAKERS_DIR):
        self.directory = Path(directory)
        self._cache_key = None
        self._cache = {}

    def _signature(self):
        """Identity of every voiceprint file, so load() can skip re-parsing
        when nothing changed. save() replaces files, which changes st_ino."""
        try:
            entries = [e for e in os.scandir(self.directory)
                       if e.name.endswith('.json') and e.is_file()]
        except OSError:
            return ()
        signature = []
        for entry in entries:
            try:
                st = entry.stat()
            except OSError:
                continue
            signature.append((entry.name, st.st_ino, st.st_mtime_ns, st.st_size))
        return tuple(sorted(signature))

    def load(self):
        """name -> {embedding, meta}. Picks up enrollments and removals
        without a restart; re-parses only when a file changed."""
        key = self._signature()
        if key != self._cache_key:
            self._cache = self._read_all()
            self._cache_key = key
        return dict(self._cache)

    def _read_all(self):
        speakers = {}
        if not self.directory.is_dir():
            return speakers
        for file in sorted(self.directory.glob('*.json')):
            try:
                data = json.loads(file.read_text(encoding='utf-8'))
                emb = np.asarray(data['embedding'], dtype=np.float64)
                norm = np.linalg.norm(emb)
                if norm == 0 or not valid_name(data.get('name')):
                    continue
                speakers[data['name']] = {
                    'embedding': emb / norm,
                    'model': data.get('model'),
                    'seconds': data.get('seconds'),
                    'created': data.get('created'),
                }
            except (ValueError, KeyError, OSError):
                continue  # unreadable file: skip, never crash the service
        return speakers

    def _stored_name(self, path):
        try:
            return json.loads(path.read_text(encoding='utf-8')).get('name')
        except (ValueError, OSError):
            return None

    def save(self, name, embedding, model, seconds):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f'{_slug(name)}.json'
        # Distinct valid names can share a slug ("Mary Ann" / "Mary-Ann");
        # never silently replace another person's voiceprint.
        existing = self._stored_name(path) if path.exists() else None
        if existing is not None and existing != name:
            raise ValueError(
                f'"{name}" collides with the enrolled speaker "{existing}" — '
                'pick a more distinct name or forget the other speaker first')
        payload = json.dumps({
            'name': name,
            'model': model,
            'dim': len(embedding),
            'seconds': round(seconds, 2),
            'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'embedding': [round(float(x), 7) for x in embedding],
        })
        # Write-then-rename, so a concurrent load() never parses a
        # half-written file.
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(payload, encoding='utf-8')
        os.replace(tmp, path)
        return path

    def remove(self, name):
        path = self.directory / f'{_slug(name)}.json'
        if not path.exists():
            return False
        stored = self._stored_name(path)
        if stored is not None and stored != name:
            return False  # same slug, different person — never delete their print
        path.unlink()
        return True


class SessionMatcher:
    """Accumulates per-label centroids for one transcription session and
    decides label -> enrolled-name assignments."""

    def __init__(self, store=None):
        self.store = store or VoiceprintStore()
        self.reset()

    def reset(self):
        self.labels = {}    # label -> {'sum': weighted embedding sum, 'seconds': float}
        self.assigned = {}  # label -> name (sticky for the session)

    def identify(self, label, embedding, speech_seconds, model=None):
        """Feed one turn's embedding; returns a result dict for the caller."""
        state = self.labels.setdefault(label, {'sum': None, 'seconds': 0.0})
        if embedding is not None and speech_seconds > 0:
            weighted = embedding * speech_seconds
            state['sum'] = weighted if state['sum'] is None else state['sum'] + weighted
            state['seconds'] += speech_seconds

        if label in self.assigned:
            return {'label': label, 'name': self.assigned[label],
                    'assigned': True, 'seconds': state['seconds']}

        result = {'label': label, 'name': None, 'assigned': False,
                  'seconds': state['seconds'], 'score': None, 'margin': None}
        if state['sum'] is None or state['seconds'] < MIN_MATCH_SPEECH_S:
            return result

        enrolled = self.store.load()
        if model is not None:
            # Voiceprints from another embedding model live in a different
            # space. Prints without a recorded model are accepted.
            enrolled = {n: info for n, info in enrolled.items()
                        if info.get('model') in (None, model)}
        if not enrolled:
            return result

        centroid = state['sum'] / np.linalg.norm(state['sum'])
        scores = {name: float(centroid @ info['embedding'])
                  for name, info in enrolled.items()}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_name, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        result['score'] = round(best, 4)
        result['margin'] = round(best - second, 4)

        taken = best_name in self.assigned.values()
        if best >= THRESHOLD and (best - second) >= MARGIN and not taken:
            self.assigned[label] = best_name
            result['name'] = best_name
            result['assigned'] = True
        return result
