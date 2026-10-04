// The .env format shared by the server and the accuracy tools: KEY=value
// lines with an optional `export ` prefix. Matching surrounding quotes are
// stripped, since a kept quote would reach the provider and read as a
// rejected key. Anything else (comments, blank lines) is ignored.
//
// The "11LABS_API_KEY" spelling is renamed to ELEVENLABS_API_KEY rather than
// aliased, so a key removed in the UI does not come back from the alias when
// the server next boots.

function parseEnv(text) {
  const env = {};
  for (const line of String(text).split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.+?)\s*$/);
    if (!match) continue;
    let value = match[2];
    const quoted = value.match(/^(["'])(.*)\1$/);
    if (quoted) value = quoted[2];
    env[match[1]] = value;
  }
  if (env['11LABS_API_KEY'] && !env.ELEVENLABS_API_KEY) {
    env.ELEVENLABS_API_KEY = env['11LABS_API_KEY'];
  }
  delete env['11LABS_API_KEY'];
  return env;
}

module.exports = { parseEnv };
