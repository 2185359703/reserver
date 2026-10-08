'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const {extractProfile} = require('./extract_profile.cjs');
const {buildRequest} = require('../aliyun_log3_node.cjs');
const root = path.join(__dirname, '..');
const [batchId, sampleId] = process.argv.slice(2);
if (!/^\d{8}-[a-f0-9]{8}$/.test(batchId) || !/^sample-\d{2}$/.test(sampleId)) throw new Error('Invalid sample identifiers');
const dir = path.join(root, '.private/cohort', batchId, sampleId);
const publicDir = path.join(root, 'analysis/samples', batchId);
const capture = JSON.parse(fs.readFileSync(path.join(dir, 'capture.json'), 'utf8'));
const {profile, secrets, form, expectedBody} = extractProfile(capture, capture.headers, {sampleId, batchId});
fs.writeFileSync(path.join(dir, 'profile.json'), JSON.stringify(profile, null, 2), {mode:0o600});
fs.writeFileSync(path.join(dir, 'secrets.json'), JSON.stringify(secrets, null, 2), {mode:0o600});
const calculated = buildRequest(profile, secrets, {timestamp:profile.capturedTimestamp, nonce:profile.capturedNonce});
const parity = {
  data: calculated.form.Data === form.Data,
  signature: calculated.form.Signature === form.Signature,
  body: calculated.body === expectedBody,
};
if (!Object.values(parity).every(Boolean)) throw new Error('Sample failed browser/local parity');
const sent = spawnSync('python', [path.join(__dirname, 'send_fresh_sample.py'), dir], {cwd:root, encoding:'utf8', timeout:60000});
if (sent.error) throw sent.error;
if (sent.status !== 0) throw new Error(`Local request failed: ${sent.stderr.trim()}`);
const local = JSON.parse(sent.stdout);
const browserRequest = capture.requests.find(r => new URLSearchParams(r.body).get('Action') === 'Log3');
const hash = value => createHash('sha256').update(value).digest('hex');
const events = Object.fromEntries(Object.entries(profile.events).filter(([,v])=>Array.isArray(v)).map(([k,v])=>[k,v.length]));
const record = {
  id:sampleId, batchId, plan:capture.plan, environment:capture.environment,
  sgScripts:capture.scripts.filter(s=>s.includes('/dynamicJS/')),
  feilinScripts:capture.scripts.filter(s=>s.includes('/FeiLin/')),
  sdk:capture.sdk || {url:capture.scripts.find(s=>s.includes('/FeiLin/')),controlled:false},
  sessionSha256:hash(profile.sessionId), deviceDataSha256:hash(profile.deviceData),
  eventsSha256:hash(JSON.stringify(profile.events)), eventCounts:events,
  gatherCost:profile.gatherCost, dataCharacters:form.Data.length,
  browser:{httpStatus:browserRequest.status, code:browserRequest.response?.Code,
    resultObject:typeof browserRequest.response?.ResultObject==='boolean'?browserRequest.response.ResultObject:null,
    nonce:form.SignatureNonce,dataSha256:hash(form.Data),bodySha256:hash(expectedBody)},
  parity, local,
};
const manifestPath=path.join(publicDir,'manifest.json');
const manifest=fs.existsSync(manifestPath)?JSON.parse(fs.readFileSync(manifestPath,'utf8')):{batchId,targetCount:15,samples:[]};
manifest.samples=manifest.samples.filter(s=>s.id!==sampleId).concat(record).sort((a,b)=>a.id.localeCompare(b.id));
fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2));
console.log(JSON.stringify({id:sampleId,count:manifest.samples.length,locale:record.environment.language,
  screen:record.environment.screen,eventCounts:events,dataCharacters:record.dataCharacters,
  parity, browser:record.browser.httpStatus+'/'+record.browser.code+'/'+record.browser.resultObject,
  local:local.httpStatus+'/'+local.code+'/'+local.resultObject}));
