# Windows package third-party notices

The Windows package includes sherpa-onnx 1.13.4, copyright the k2-fsa
authors, distributed under the Apache License 2.0:
https://www.apache.org/licenses/LICENSE-2.0

sherpa-onnx source and licensing information:
https://github.com/k2-fsa/sherpa-onnx

The optional offline speech model (Nemotron 3.5 ASR streaming 0.6b, int8
export) is NOT distributed with this package. It is downloaded at the user's
explicit request, from the export's Hugging Face repository, under the terms
published there (NVIDIA Open Model License):
https://huggingface.co/csukuangfj2/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11

The package includes the WeSpeaker ResNet34 speaker-embedding model
(wespeaker-voxceleb-resnet34-LM), distributed under the Apache License 2.0,
for optional named-speaker identification:
https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM

The package also includes liblouis, Python, Node.js, pyserial, websockets,
numpy, onnxruntime, and the Node `ws` module. Their license metadata is
preserved in the staged vendor or package directories. No third-party
component changes Dotify's own license.
