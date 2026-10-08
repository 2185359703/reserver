'use strict';
const fs = require('node:fs');
const path = require('node:path');
const codec = require('../aliyun_codec.cjs');
const root = path.join(__dirname, '..');
const capture = JSON.parse(fs.readFileSync(path.join(root, '.private/browser_capture.json'), 'utf8'));
const results = [];
let dataEncryption;
for (const request of capture.requests) {
  const form = Object.fromEntries(new URLSearchParams(request.body));
  if (!['Log2', 'Log3'].includes(form.Action)) continue;
  const event = capture.aes.find(e => e.method === 'encrypt' && e.result === form.Data);
  if (!event) throw new Error(`Missing AES input for ${form.Action}`);
  const result = {
    action: form.Action,
    signature_matches: codec.signature(form, capture.secret) === form.Signature,
    ciphertext_matches: codec.encrypt(event.message, event.key, event.iv) === form.Data,
    serializer_matches: codec.serializeForm(form) === request.body,
    plaintext_characters: event.message.length,
  };
  results.push(result);
  if (form.Action === 'Log3') {
    dataEncryption = { dataKeyHex: event.key, ivHex: event.iv };
    fs.writeFileSync(path.join(root, '.private/log3_plaintext.txt'), event.message, {mode: 0o600});
    fs.writeFileSync(path.join(root, '.private/log3_form.json'), JSON.stringify(form, null, 2), {mode: 0o600});
  }
}
const capturePath = process.argv[2];
if (capturePath) {
  const curl = fs.readFileSync(capturePath, 'utf8');
  const form = Object.fromEntries(new URLSearchParams(curl.match(/--data-raw\s+'([\s\S]*?)'/)[1]));
  const original = { action: form.Action, signature_matches: codec.signature(form, capture.secret) === form.Signature };
  try {
    if (!dataEncryption) throw new Error('Missing Log3 encryption capture');
    const plaintext = codec.decrypt(form.Data, dataEncryption.dataKeyHex, dataEncryption.ivHex);
    original.decrypts = true;
    original.plaintext_characters = plaintext.length;
    original.ciphertext_matches = codec.encrypt(plaintext, dataEncryption.dataKeyHex, dataEncryption.ivHex) === form.Data;
    fs.writeFileSync(path.join(root, '.private/original_plaintext.txt'), plaintext, {mode: 0o600});
    fs.writeFileSync(path.join(root, '.private/original_form.json'), JSON.stringify(form, null, 2), {mode: 0o600});
  } catch {
    original.decrypts = false;
  }
  results.push({source: 'user_capture', ...original});
}
console.log(JSON.stringify(results, null, 2));
if (results.some(r => !r.signature_matches || r.ciphertext_matches === false || r.serializer_matches === false)) process.exitCode = 1;
