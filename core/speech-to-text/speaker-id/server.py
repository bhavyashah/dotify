# Local speaker-identification service (no cloud, no audio ever stored).
#
# Sits next to the Node speech server (server.js) on 127.0.0.1:8792, which POSTs
# each finalized diarized turn's PCM here with its diarization label; this
# service embeds the audio, accumulates per-label evidence, and answers with
# an enrolled speaker's name once confident. It also handles enrollment
# (embed a ~20 s clip -> voiceprint JSON in speakers/) so the browser UI can
# manage speakers through the speech server's /api/speakers proxy.
#
#   GET    /health              -> {ok, model, speakers}
#   GET    /speakers            -> {speakers: [{name, seconds, created}]}
#   POST   /speakers/<name>     body: raw PCM16 mono 24 kHz -> enroll
#   DELETE /speakers/<name>     -> forget a voiceprint
#   POST   /identify?label=A    body: raw PCM16 mono 24 kHz (one turn)
#   POST   /session/reset       -> new transcription session
#
# Run:  python server.py   (first: pip install -r requirements.txt,
#                           then:  python download_model.py)

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import matcher

PORT = int(os.environ.get('DOTIFY_SPEAKER_ID_PORT', '8792'))
MAX_BODY = 8 * 1024 * 1024  # 30 s enrollment @ 24 kHz PCM16 is ~1.4 MB

_lock = threading.Lock()
_store = matcher.VoiceprintStore()
_session = matcher.SessionMatcher(_store)
_embedder = None
_embedder_error = None


def _load_embedder():
    global _embedder, _embedder_error
    try:
        import embedder as embedder_mod
        _embedder = embedder_mod.Embedder()
        print(f'Speaker model loaded: {_embedder.name}')
    except Exception as error:  # missing model/onnxruntime: serve errors, don't die
        _embedder_error = str(error)
        print(f'Speaker model unavailable: {_embedder_error}', file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _json(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        # Error paths answer BEFORE reading the POST body (503 model-missing,
        # 400 bad label, oversized clip). This handler speaks HTTP/1.1
        # keep-alive, so leaving those bytes unread would make the next
        # request line parse out of the middle of raw PCM — desyncing the
        # connection and handing a later request a stale response. Close
        # instead whenever a request body was left unread.
        announced = int(self.headers.get('Content-Length') or 0)
        if announced > 0 and not getattr(self, '_body_read', False):
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        data = self.rfile.read(length)
        self._body_read = True
        return data

    def _speaker_name(self, path):
        prefix = '/speakers/'
        if not path.startswith(prefix):
            return None
        name = unquote(path[len(prefix):]).strip()
        return name if matcher.valid_name(name) else None

    def do_GET(self):
        self._body_read = False  # handler instances persist across keep-alive requests
        path = urlparse(self.path).path
        if path in ('/health', '/speakers'):
            speakers = [
                {'name': name, 'seconds': info['seconds'], 'created': info['created']}
                for name, info in sorted(_store.load().items())
            ]
            payload = {'speakers': speakers}
            if path == '/health':
                payload.update({
                    'ok': _embedder is not None,
                    'model': _embedder.name if _embedder else None,
                    'error': _embedder_error,
                })
            self._json(200, payload)
        else:
            self._json(404, {'error': 'not found'})

    def do_DELETE(self):
        self._body_read = False  # handler instances persist across keep-alive requests
        name = self._speaker_name(urlparse(self.path).path)
        if not name:
            self._json(400, {'error': 'invalid speaker name'})
            return
        removed = _store.remove(name)
        self._json(200 if removed else 404,
                   {'removed': removed, 'name': name})

    def do_POST(self):
        self._body_read = False  # handler instances persist across keep-alive requests
        url = urlparse(self.path)
        path = url.path

        if path == '/session/reset':
            with _lock:
                _session.reset()
            self._json(200, {'reset': True})
            return

        if _embedder is None:
            self._json(503, {'error': _embedder_error or 'model not loaded'})
            return

        if path == '/identify':
            label = (parse_qs(url.query).get('label') or [''])[0]
            if not label or len(label) > 16:
                self._json(400, {'error': 'missing/invalid label'})
                return
            body = self._body()
            if body is None:
                self._json(400, {'error': 'missing audio body'})
                return
            embedding, speech_s = _embedder.embed_turn(body)
            with _lock:
                result = _session.identify(label, embedding, speech_s,
                                           model=_embedder.name)
            self._json(200, result)
            return

        name = self._speaker_name(path)
        if name:
            body = self._body()
            if body is None:
                self._json(400, {'error': 'missing audio body'})
                return
            embedding, speech_s = _embedder.embed_enrollment(body)
            if embedding is None or speech_s < matcher.MIN_ENROLL_SPEECH_S:
                self._json(400, {
                    'error': f'Only {speech_s:.1f}s of clear speech detected — '
                             f'need at least {matcher.MIN_ENROLL_SPEECH_S:.0f}s. '
                             f'Please record again, speaking continuously.',
                    'seconds': round(speech_s, 1),
                })
                return
            try:
                _store.save(name, embedding, _embedder.name, speech_s)
            except ValueError as error:  # slug collision with another speaker
                self._json(409, {'error': str(error)})
                return
            self._json(200, {'name': name, 'seconds': round(speech_s, 1)})
            return

        self._json(404, {'error': 'not found'})

    def log_message(self, fmt, *args):  # quiet: one line per request is noise
        pass


def main():
    _load_embedder()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'Speaker-ID service: http://127.0.0.1:{PORT} '
          f'(voiceprints in {_store.directory})')
    server.serve_forever()


if __name__ == '__main__':
    main()
