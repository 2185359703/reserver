'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {setTimeout: pause} = require('node:timers/promises');
const {extractProfile} = require('./extract_profile.cjs');
const {buildRequest, loadPairedProfile} = require('../aliyun_log3_node.cjs');
const root = path.join(__dirname, '..');
async function main() {
  const index=JSON.parse(fs.readFileSync(path.join(root,'.private/cohort_index.json'),'utf8'));
  let parity=0;
  for(const entry of index.samples) {
    const dir=path.dirname(path.resolve(root,entry.profile));
    const capture=JSON.parse(fs.readFileSync(path.join(dir,'capture.json'),'utf8'));
    const {profile,secrets,expectedBody}=extractProfile(capture,capture.headers,{sampleId:entry.id});
    const calculated=buildRequest(profile,secrets,{timestamp:profile.capturedTimestamp,nonce:profile.capturedNonce});
    if(calculated.body!==expectedBody) throw new Error(`${entry.id} full wire parity failed`);
    const fixed=loadPairedProfile({sample:entry.id});
    if(fixed.profile.sampleId!==entry.id || fixed.secrets.signingSecret!==secrets.signingSecret) throw new Error('Wrong sample pairing');
    parity++;
  }
  const distribution={},nonces=new Set(),bodies=new Set(),data=new Set();
  for(let i=0;i<45;i++) {
    const pair=loadPairedProfile({sample:'random'});
    const request=buildRequest(pair.profile,pair.secrets);
    distribution[request.sampleId]=(distribution[request.sampleId]||0)+1;
    nonces.add(request.form.SignatureNonce);
    bodies.add(createHash('sha256').update(request.body).digest('hex'));
    data.add(createHash('sha256').update(request.form.Data).digest('hex'));
    await pause(2);
  }
  if(nonces.size!==45||bodies.size!==45||data.size!==45) throw new Error('Duplicate fields in randomized requests');
  const result={batchId:index.batchId,allSampleWireParity:parity,randomizedOfflineRequests:45,
    uniqueNonces:nonces.size,uniqueBodies:bodies.size,uniqueData:data.size,selectionDistribution:distribution,
    sentNetworkRequests:0};
  fs.writeFileSync(path.join(root,'analysis/samples',index.batchId,'pool_validation.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
