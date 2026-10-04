// Download manager for the optional offline speech model (Nemotron streaming
// ASR, int8), exposed by the server over /api/offline-model. The model is
// ~650 MB, so it is downloaded into the user's data folder, never installed.
// offline-model.json is the one manifest; the Windows decode server
// (Windows/overlay/local_speech_server.py) reads it too. Presence plus exact
// byte sizes is the ready check, so a torn download never half-loads.
// Downloads resume with HTTP Range requests.

const fs = require('fs');
const path = require('path');
const { pipeline } = require('stream/promises');

const MANIFEST = require('./offline-model.json');

function createOfflineModel({
  modelsRoot,
  manifest = MANIFEST,
  fetchImpl = fetch,
} = {}) {
  const dir = path.join(modelsRoot, manifest.dirName);
  const totalBytes = manifest.files.reduce((sum, f) => sum + f.bytes, 0);

  let downloading = false;
  let aborter = null;
  // Bytes the in-flight download has streamed; null when idle, when status
  // reads the disk instead (so a hand-copied model is recognized).
  let liveBytes = null;
  let lastError = null;

  const fileComplete = (f) => {
    try { return fs.statSync(path.join(dir, f.name)).size === f.bytes; }
    catch { return false; }
  };

  const diskBytes = () => manifest.files.reduce((sum, f) => {
    // A partial larger than the file is a different export: count zero.
    try {
      const size = fs.statSync(path.join(dir, f.name)).size;
      return sum + (size <= f.bytes ? size : 0);
    } catch { return sum; }
  }, 0);

  const ready = () => manifest.files.every(fileComplete);

  function status() {
    const downloadedBytes = downloading && liveBytes !== null ? liveBytes : diskBytes();
    return {
      label: manifest.label,
      ready: ready(),
      downloading,
      totalBytes,
      downloadedBytes,
      percent: Math.floor((downloadedBytes * 100) / totalBytes),
      ...(lastError ? { error: lastError } : {}),
    };
  }

  async function run(signal) {
    fs.mkdirSync(dir, { recursive: true });
    liveBytes = diskBytes();
    for (const f of manifest.files) {
      const target = path.join(dir, f.name);
      if (fileComplete(f)) continue;
      let have = 0;
      try {
        const size = fs.statSync(target).size;
        if (size > f.bytes) fs.rmSync(target); // stale different-export partial
        else have = size;
      } catch { /* nothing on disk yet */ }
      const response = await fetchImpl(manifest.baseUrl + f.name, {
        signal,
        headers: have > 0 ? { Range: `bytes=${have}-` } : {},
      });
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status} for ${f.name}`);
      }
      const append = have > 0 && response.status === 206;
      if (!append && have > 0) liveBytes -= have; // server ignored the Range
      const out = fs.createWriteStream(target, { flags: append ? 'a' : 'w' });
      async function* counted(body) {
        for await (const chunk of body) {
          liveBytes += chunk.length;
          yield chunk;
        }
      }
      // pipeline owns backpressure, teardown, and abort for both sides.
      await pipeline(counted(response.body), out, { signal });
      if (!fileComplete(f)) {
        throw new Error(
          `${f.name} ended at ${fs.statSync(target).size} bytes, expected ${f.bytes}`);
      }
    }
  }

  function startDownload() {
    if (downloading || ready()) return status();
    downloading = true;
    lastError = null;
    aborter = new AbortController();
    run(aborter.signal).catch((error) => {
      // A cancel is the user's own action, not a failure to report back.
      if (!aborter.signal.aborted) {
        lastError = error && error.message ? error.message : String(error);
      }
    }).finally(() => {
      downloading = false;
      aborter = null;
      liveBytes = null;
    });
    return status();
  }

  function cancel() {
    if (aborter) aborter.abort();
    return status();
  }

  function remove() {
    if (downloading) throw new Error('Cancel the download before deleting the model.');
    lastError = null;
    fs.rmSync(dir, { recursive: true, force: true });
    return status();
  }

  return { dir, status, startDownload, cancel, remove };
}

module.exports = { createOfflineModel };
