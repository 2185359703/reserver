'use strict';
const fs=require('node:fs');const path=require('node:path');const {createHash}=require('node:crypto');
const {setTimeout:pause}=require('node:timers/promises');
const {loadProfile,buildRequest,useServerConfig}=require('../../aliyun_initcaptcha_node.cjs');
const root=path.join(__dirname,'../..');
async function main(){
  const index=JSON.parse(fs.readFileSync(path.join(root,'.private/initcaptcha/index.json'),'utf8'));
  const selected={},tokens=new Set(),nonces=new Set(),bodies=new Set();
  for(let i=0;i<45;i++){
    const pair=loadProfile({sample:'random'});
    const record=index.samples.find(s=>s.id===pair.profile.sampleId);
    const capture=JSON.parse(fs.readFileSync(path.join(path.dirname(path.resolve(root,record.profile)),'capture.json'),'utf8'));
    const first=capture.requests.find(r=>new URLSearchParams(r.body).has('DeviceData')&&r.response?.Success);
    const updated=useServerConfig(pair.profile,pair.secrets,first.response.DeviceConfig);
    const request=buildRequest(updated.profile,updated.secrets,{certifyId:pair.profile.capturedCertifyId});
    selected[request.sampleId]=(selected[request.sampleId]||0)+1;
    tokens.add(createHash('sha256').update(request.form.DeviceToken).digest('hex'));
    nonces.add(request.form.SignatureNonce);bodies.add(createHash('sha256').update(request.body).digest('hex'));
    await pause(2);
  }
  if(tokens.size!==45||nonces.size!==45||bodies.size!==45)throw new Error('随机请求出现重复');
  const result={batchId:index.batchId,offlineRequests:45,uniqueTokens:tokens.size,uniqueNonces:nonces.size,uniqueBodies:bodies.size,selectionDistribution:selected,networkRequests:0};
  fs.writeFileSync(path.join(__dirname,'samples',index.batchId,'random_validation.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
