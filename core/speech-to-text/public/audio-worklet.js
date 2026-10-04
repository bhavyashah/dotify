// Captures mono audio and posts Float32 chunks to the main thread.
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      // Copy: the underlying buffer is reused by the audio engine.
      this.port.postMessage(new Float32Array(channel));
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
