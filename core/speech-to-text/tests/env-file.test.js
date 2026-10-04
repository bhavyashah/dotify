const { test } = require('node:test');
const assert = require('node:assert');
const { parseEnv } = require('../env-file');

test('parseEnv reads KEY=value lines and skips everything else', () => {
  const env = parseEnv([
    '# a comment',
    '',
    'OPENAI_API_KEY=sk-plain',
    'export DEEPGRAM_API_KEY=dg-exported',
    'not a setting',
    'lowercase_key=ignored',
  ].join('\r\n'));
  assert.deepStrictEqual(env, {
    OPENAI_API_KEY: 'sk-plain',
    DEEPGRAM_API_KEY: 'dg-exported',
  });
});

test('parseEnv trims surrounding whitespace and matching quotes only', () => {
  const env = parseEnv([
    'A_KEY=  spaced value  ',
    'B_KEY="double quoted"',
    "C_KEY='single quoted'",
    'D_KEY="mismatched\'',
  ].join('\n'));
  assert.strictEqual(env.A_KEY, 'spaced value');
  assert.strictEqual(env.B_KEY, 'double quoted');
  assert.strictEqual(env.C_KEY, 'single quoted');
  assert.strictEqual(env.D_KEY, '"mismatched\'');
});

test('parseEnv renames the 11LABS spelling instead of aliasing it', () => {
  assert.deepStrictEqual(parseEnv('11LABS_API_KEY=xi-key'),
    { ELEVENLABS_API_KEY: 'xi-key' });
  // The canonical name wins, and the alias still does not survive.
  assert.deepStrictEqual(
    parseEnv('ELEVENLABS_API_KEY=canonical\n11LABS_API_KEY=alias'),
    { ELEVENLABS_API_KEY: 'canonical' });
});
