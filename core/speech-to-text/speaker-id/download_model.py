# One-time model fetch. Downloads a WeSpeaker ONNX speaker-embedding model
# into models/ and writes models/model.json so server.py knows what to load.
#
#   python download_model.py            # default model (resnet34)
#
# resnet34: WeSpeaker ResNet34 with large-margin fine-tune, VoxCeleb-trained,
# Apache-2.0, 26.5 MB, 256-dim embeddings, ~50 ms per turn on one CPU thread.
#
# Chosen over WeSpeaker's CAM++ ONNX export, which produces length-unstable
# embeddings: nested prefixes of the same audio embed near-orthogonally at
# some lengths, where ResNet34 scores 0.97+. Run that probe (see README)
# before trusting any model added here.

import json
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / 'models'

MODELS = {
    'resnet34': {
        'file': 'resnet34_voxceleb_lm.onnx',
        'dim': 256,
        'urls': [
            'https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx',
        ],
    },
}


def download(name):
    spec = MODELS[name]
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = MODELS_DIR / spec['file']
    last_error = None
    for url in spec['urls']:
        try:
            print(f'Downloading {name} from {url} ...')
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            if len(data) < 1_000_000:
                raise ValueError(f'suspiciously small download ({len(data)} bytes)')
            target.write_bytes(data)
            (MODELS_DIR / 'model.json').write_text(json.dumps({
                'name': name, 'file': spec['file'], 'dim': spec['dim'], 'url': url,
            }, indent=2), encoding='utf-8')
            print(f'Saved {target} ({len(data) / 1e6:.1f} MB). '
                  f'The speaker-ID service will use it on next start.')
            return
        except Exception as error:
            last_error = error
            print(f'  failed: {error}')
    raise SystemExit(f'Could not download {name}: {last_error}')


if __name__ == '__main__':
    choice = sys.argv[1] if len(sys.argv) > 1 else 'resnet34'
    if choice not in MODELS:
        raise SystemExit(f'Unknown model "{choice}". Options: {", ".join(MODELS)}')
    download(choice)
