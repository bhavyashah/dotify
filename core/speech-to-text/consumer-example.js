// Minimal consumer of the finalized text stream (ws://localhost:8788/finalized):
//
//   {"type":"final","id":"x-1","text":"...","ts":"..."}   hardened segment,
//       never revised again. An id seen earlier as "soft" means this final
//       replaces that segment's text; a new id is a new segment.
//   {"type":"soft","id":"x-2","text":"...","ts":"..."}    revisable segment:
//       the stable prefix of the engine's live hypothesis, sent early.
//   {"type":"revise","revise":"x-2","text":"...","ts":"..."}  new text for a
//       still-soft segment ("" withdraws it).
//   {"type":"latency","mode":"balanced","render":"eager"|"confirmed","ts":"..."}
//       whether soft text may be shown before it hardens.
//
// A consumer that handles only "final" gets finalized text and nothing else.
const { WebSocket } = require('ws');

const ws = new WebSocket('ws://localhost:8788/finalized');
const soft = new Set(); // ids currently held as revisable text

ws.on('open', () => console.log('Connected to finalized stream.'));
ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  if (msg.type === 'final') {
    if (soft.delete(msg.id)) console.log(`FINAL (hardens ${msg.id}):`, msg.text);
    else console.log('FINAL:', msg.text);
  } else if (msg.type === 'soft') {
    soft.add(msg.id);
    console.log(`SOFT  (${msg.id}):`, msg.text);
  } else if (msg.type === 'revise' && soft.has(msg.revise)) {
    console.log(`REVISE (${msg.revise}):`, msg.text || '(withdrawn)');
  }
});
ws.on('close', () => console.log('Stream closed.'));
