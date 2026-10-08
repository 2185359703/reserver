'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
function extractProfile(capture, headers = {}, metadata = {}) {
const request = capture.requests.find(r => new URLSearchParams(r.body).get('Action') === 'Log3');
if (!request) throw new Error('Capture does not contain a Log3 request');
const form = Object.fromEntries(new URLSearchParams(request.body));
function aesInput(ciphertext) {
  const e = capture.aes.find(e => e.method === 'encrypt' && e.result === ciphertext);
  if (!e || e.keyIsString) throw new Error('Missing raw-key AES input in capture');
  return e;
}
const outerEncryption = aesInput(form.Data);
const outer = outerEncryption.message.split('#');
const inner = Buffer.from(outer[6], 'base64').toString('utf8').split('#');
const telemetry = Buffer.from(inner[2], 'base64').toString('utf8').split('#');
if (outer.length !== 7 || inner.length !== 3 || telemetry.length !== 6) {
  throw new Error('Unexpected Log3 schema; recapture before using this SDK build');
}
const flag = aesInput(outer[2]);
const events = aesInput(telemetry[1]);
const appName = aesInput(telemetry[2]);
const clientVersion = aesInput(telemetry[3]);
const timestamp = aesInput(telemetry[5]);
const secrets = {
  signingSecret: capture.secret,
  aes: Object.fromEntries(Object.entries({outer: outerEncryption, flag, events, appName, clientVersion, timestamp})
    .map(([name, e]) => [name, {keyHex: e.key, ivHex: e.iv}])),
};
const staticFields = {...form};
delete staticFields.Data;
delete staticFields.SignatureNonce;
delete staticFields.Signature;
const profile = {
  ...metadata,
  schemaVersion: 1,
  source: 'Camoufox FeiLin 1.5.1 runtime AES and XHR capture',
  capturedAt: new Date(Number(timestamp.message)).toISOString(),
  endpoint: 'https://cloudauth-device-dualstack.cn-shanghai.aliyuncs.com/',
  headers,
  fields: staticFields,
  appKey: outer[0], deviceType: outer[1], flag: flag.message,
  appVersion: outer[3], environment: outer[4], gatherCost: outer[5],
  recordType: inner[0], deviceData: inner[1], sessionId: telemetry[0],
  events: JSON.parse(events.message), appName: appName.message,
  clientVersion: clientVersion.message, extra: telemetry[4],
  capturedTimestamp: Number(timestamp.message), capturedNonce: form.SignatureNonce,
};
return {profile, secrets, form, expectedBody: request.body};
}

function main() {
const privateDir = path.join(root, '.private');
const capture = JSON.parse(fs.readFileSync(path.join(privateDir, 'browser_capture.json'), 'utf8'));
const headersPath = path.join(privateDir, 'browser_headers.json');
const headers = fs.existsSync(headersPath) ? JSON.parse(fs.readFileSync(headersPath, 'utf8')) : {};
const {profile, secrets} = extractProfile(capture, headers);
fs.writeFileSync(path.join(privateDir, 'secrets.json'), JSON.stringify(secrets, null, 2), {mode: 0o600});
fs.writeFileSync(path.join(privateDir, 'runtime_profile.json'), JSON.stringify(profile, null, 2), {mode: 0o600});
console.log(JSON.stringify({profile: '.private/runtime_profile.json', secrets: '.private/secrets.json', schema: [7, 3, 6], eventKeys: Object.keys(profile.events)}, null, 2));
}
module.exports = {extractProfile};
if (require.main === module) main();
