'use strict';
// Browser-free wire construction from a real Camoufox device/event profile.
// Public SDK parameters live in config; captured device profiles stay private.
const fs = require('node:fs');
const path = require('node:path');
const {createHash, randomInt} = require('node:crypto');
const {parseArgs} = require('node:util');
const codec = require('./aliyun_codec.cjs');

function buildRequest(profile, secrets, options = {}) {
  if (profile.schemaVersion !== 1 || profile.fields.Action !== 'Log3') {
    throw new Error('Unsupported profile; expected a captured Log3 schemaVersion 1');
  }
  const now = options.timestamp ?? Date.now();
  if (!Number.isSafeInteger(now) || now <= 0) throw new Error('Invalid request timestamp');
  const events = options.events ?? profile.events;
  const encrypt = (role, text) => {
    const config = secrets.aes[role];
    return codec.encrypt(String(text), config.keyHex, config.ivHex);
  };
  const base64 = text => Buffer.from(text, 'utf8').toString('base64');
  // SDK-observed schema: AES(outer + Base64(inner + Base64(telemetry))).
  const telemetry = [
    profile.sessionId,
    encrypt('events', JSON.stringify(events)),
    encrypt('appName', profile.appName),
    encrypt('clientVersion', profile.clientVersion),
    profile.extra,
    encrypt('timestamp', now),
  ].join('#');
  const inner = [profile.recordType, profile.deviceData, base64(telemetry)].join('#');
  const plaintext = [
    profile.appKey, profile.deviceType, encrypt('flag', profile.flag),
    profile.appVersion, profile.environment, profile.gatherCost, base64(inner),
  ].join('#');
  const data = encrypt('outer', plaintext);
  // Keep the SDK's insertion order for the wire, while sorting for HMAC.
  const fields = {...profile.fields, Data: data};
  const form = codec.signedForm(fields, secrets.signingSecret, options.nonce);
  const body = codec.serializeForm(form);
  return {url: profile.endpoint, headers: {...profile.headers}, body, form, timestamp: now,
    sampleId: profile.sampleId || 'baseline', batchId: profile.batchId || null};
}

function loadPairedProfile(options = {}) {
  const read = name => JSON.parse(fs.readFileSync(name, 'utf8'));
  const fallbackProfile = path.join(__dirname, '.private/runtime_profile.json');
  const privateSecrets = path.join(__dirname, '.private/secrets.json');
  const fallbackSecrets = fs.existsSync(privateSecrets) ? privateSecrets : path.join(__dirname, 'config/sdk_keys.json');
  if (options.profile || options.secrets) {
    if (!options.profile || !options.secrets) throw new Error('Specify --profile and --secrets together to keep the sample paired');
    return {profile:read(options.profile), secrets:read(options.secrets)};
  }
  const sample = options.sample || 'random';
  const indexPath = path.join(__dirname, '.private/cohort_index.json');
  if (sample === 'baseline' || !fs.existsSync(indexPath)) {
    if (!['random', 'baseline'].includes(sample)) throw new Error(`Unknown sample: ${sample}`);
    return {profile:read(fallbackProfile), secrets:read(fallbackSecrets)};
  }
  const index = read(indexPath);
  if (!Array.isArray(index.samples) || index.samples.length === 0) throw new Error('Sample pool is empty');
  const entry = sample === 'random' ? index.samples[randomInt(index.samples.length)] : index.samples.find(s=>s.id===sample);
  if (!entry) throw new Error(`Unknown sample: ${sample}`);
  // Choose one complete pair; never mix another sample's device state or keys.
  return {profile:read(path.resolve(__dirname, entry.profile)), secrets:read(path.resolve(__dirname, entry.secrets))};
}

function summarize(request) {
  return {
    method: 'POST', url: request.url, action: request.form.Action,
    sampleId: request.sampleId, batchId: request.batchId,
    timestamp: request.timestamp, bodyBytes: Buffer.byteLength(request.body),
    dataCharacters: request.form.Data.length,
    dataSha256: createHash('sha256').update(request.form.Data).digest('hex'),
    bodySha256: createHash('sha256').update(request.body).digest('hex'),
    nonce: request.form.SignatureNonce,
    localCalculation: true, browserRequiredForRequest: false,
    deviceAndEventsSource: 'captured profile',
  };
}

function main() {
  const {values} = parseArgs({options: {
    profile: {type:'string'}, secrets: {type:'string'},
    sample: {type:'string', default:'random'},
    events: {type:'string'}, timestamp: {type:'string'}, nonce: {type:'string'},
    summary: {type:'boolean', default:false},
  }});
  const read = name => JSON.parse(fs.readFileSync(name, 'utf8'));
  const selected = loadPairedProfile(values);
  const request = buildRequest(selected.profile, selected.secrets, {
    timestamp: values.timestamp === undefined ? undefined : Number(values.timestamp),
    nonce: values.nonce,
    events: values.events ? read(values.events) : undefined,
  });
  // Default stdout is intended for the Python subprocess, not application logs.
  process.stdout.write(JSON.stringify(values.summary ? summarize(request) : request));
}

module.exports = {buildRequest, summarize, loadPairedProfile};
if (require.main === module) {
  try {main();} catch (error) {
    process.stderr.write(`Local calculation failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
