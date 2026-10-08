'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const root = path.join(__dirname, '..');
const batchId = process.argv[2];
if (!/^\d{8}-[a-f0-9]{8}$/.test(batchId)) throw new Error('Invalid batch ID');
const publicDir = path.join(root, 'analysis/samples', batchId);
const manifestPath = path.join(publicDir, 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
if (manifest.samples.length !== manifest.targetCount) throw new Error('Sample cohort is incomplete');
const hash = value => createHash('sha256').update(value).digest('hex');
const index = {schemaVersion:1, batchId, selection:'crypto.randomInt, choose complete paired profile', samples:[]};
for (const sample of manifest.samples) {
  const dir = path.join(root, '.private/cohort', batchId, sample.id);
  const capture = JSON.parse(fs.readFileSync(path.join(dir, 'capture.json'), 'utf8'));
  sample.feilinScripts = capture.scripts.filter(s=>s.includes('/FeiLin/'));
  sample.sdk = capture.sdk || {url:sample.feilinScripts[0],controlled:false,selection:'natural'};
  sample.sdk.variant ||= sample.sdk.url.match(/feilin(\d+)\./)[1];
  sample.environmentSha256 = hash(JSON.stringify(sample.environment));
  if (!Object.values(sample.parity).every(Boolean)) throw new Error(`${sample.id} parity failed`);
  for (const result of [sample.browser, sample.local]) {
    if (result.httpStatus!==200 || String(result.code)!=='200' || result.resultObject!==true) throw new Error(`${sample.id} request not accepted`);
  }
  if (sample.browser.dataSha256 === sample.local.dataSha256 || sample.browser.nonce === sample.local.nonce) throw new Error(`${sample.id} local request reused captured fields`);
  index.samples.push({id:sample.id,
    profile:path.relative(root,path.join(dir,'profile.json')).replaceAll('\\','/'),
    secrets:path.relative(root,path.join(dir,'secrets.json')).replaceAll('\\','/'),
    locale:sample.environment.language,sdkVariant:sample.sdk.variant});
}
const unique = fn => new Set(manifest.samples.map(fn)).size;
const frequencies = fn => manifest.samples.reduce((counts,s)=>{const k=fn(s);counts[k]=(counts[k]||0)+1;return counts;},{});
const range = fn => ({min:Math.min(...manifest.samples.map(fn)),max:Math.max(...manifest.samples.map(fn))});
manifest.summary = {
  groups:manifest.samples.length, browserAccepted:15, localAccepted:15, completeParity:15,
  locales:frequencies(s=>s.environment.language),
  sdkVariants:frequencies(s=>s.sdk.variant),
  controlledSourceGroups:manifest.samples.filter(s=>s.sdk.controlled).map(s=>s.id),
  screenResolutions:frequencies(s=>`${s.environment.screen.width}x${s.environment.screen.height}`),
  distinctEnvironments:unique(s=>s.environmentSha256),
  distinctSessions:unique(s=>s.sessionSha256),distinctDeviceData:unique(s=>s.deviceDataSha256),
  distinctEventPayloads:unique(s=>s.eventsSha256),
  distinctBrowserData:unique(s=>s.browser.dataSha256),distinctLocalData:unique(s=>s.local.dataSha256),
  distinctBrowserNonces:unique(s=>s.browser.nonce),distinctLocalNonces:unique(s=>s.local.nonce),
  dataCharacters:range(s=>s.dataCharacters),mouseMoves:range(s=>s.eventCounts.mousemove),
  keyUps:range(s=>s.eventCounts.keyup),mouseClicks:range(s=>s.eventCounts.mouseclick),
  distinctSgScripts:new Set(manifest.samples.flatMap(s=>s.sgScripts)).size,
};
for(const key of ['distinctSessions','distinctDeviceData','distinctEventPayloads','distinctBrowserData','distinctLocalData','distinctBrowserNonces','distinctLocalNonces']) {
  if(manifest.summary[key]!==manifest.samples.length) throw new Error(`Duplicate samples: ${key}`);
}
fs.writeFileSync(manifestPath, JSON.stringify(manifest,null,2));
fs.writeFileSync(path.join(root,'.private/cohort_index.json'),JSON.stringify(index,null,2),{mode:0o600});
console.log(JSON.stringify(manifest.summary,null,2));
