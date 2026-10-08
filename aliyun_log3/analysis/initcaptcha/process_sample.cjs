'use strict';
const fs=require('node:fs');const path=require('node:path');const {spawnSync}=require('node:child_process');
const {createHash}=require('node:crypto');const {extractProfile}=require('./extract_profile.cjs');
const {buildRequest,useServerConfig}=require('../../aliyun_initcaptcha_node.cjs');
const codec=require('../../aliyun_codec.cjs');
const root=path.join(__dirname,'../..');const [batch,id]=process.argv.slice(2);
if(!/^\d{8}-[a-f0-9]{8}$/.test(batch)||!/^sample-\d{2}$/.test(id))throw new Error('无效样本路径');
const dir=path.join(root,'.private/initcaptcha/cohort',batch,id);
const capture=JSON.parse(fs.readFileSync(path.join(dir,'capture.json'),'utf8'));
const {profile,secrets,initial,refresh}=extractProfile(capture,capture.headers,{sampleId:id,batchId:batch});
fs.writeFileSync(path.join(dir,'profile.json'),JSON.stringify(profile,null,2),{mode:0o600});
fs.writeFileSync(path.join(dir,'secrets.json'),JSON.stringify(secrets,null,2),{mode:0o600});
const first=buildRequest(profile,secrets,{phase:'bootstrap',timestamp:profile.capturedBootstrapTimestamp,nonce:profile.capturedBootstrapNonce});
const second=buildRequest(profile,secrets,{phase:'refresh',fixed:true,certifyId:profile.capturedCertifyId,timestamp:profile.capturedTimestamp,nonce:profile.capturedNonce});
const parity={bootstrapBody:first.body===initial.body,refreshBody:second.body===refresh.body,deviceToken:second.form.DeviceToken===profile.capturedToken};
if(!Object.values(parity).every(Boolean))throw new Error('原始与本地字节比对失败');
const p=spawnSync('python',[path.join(__dirname,'send_fresh_sample.py'),dir],{cwd:root,encoding:'utf8',timeout:70000});
if(p.status!==0)throw new Error(p.stderr||'本地请求未完成');
const local=JSON.parse(p.stdout);const hash=v=>createHash('sha256').update(v).digest('hex');
const flow=JSON.parse(fs.readFileSync(path.join(dir,'local_flow.json'),'utf8'));
const updated=useServerConfig(profile,secrets,flow.bootstrapResponse.DeviceConfig);
const generated=Buffer.from(flow.request.form.DeviceToken,'base64').toString('utf8').split('#');
const generatedFields=codec.decrypt(generated[2],updated.secrets.aes.token.keyHex,updated.secrets.aes.token.ivHex).split('#');
local.freshServerSession=generated[1]===updated.profile.token.sessionId;
local.freshServerFields=Object.keys(profile.serverConfig.fieldMap).every(i=>generatedFields[i]===updated.profile.token.fields[i]);
local.freshServerCertifyId=flow.request.form.CertifyId===flow.bootstrapResponse.CertifyId;
if(!local.freshServerSession||!local.freshServerFields||!local.freshServerCertifyId)throw new Error('新的服务端初始化状态未正确配对');
const record={id,batchId:batch,plan:capture.plan,environment:capture.environment,feilinSdk:capture.feilinSdk,
  sessionSha256:hash(profile.token.sessionId),tokenFieldsSha256:hash(JSON.stringify(profile.token.fields)),
  tokenCharacters:profile.capturedToken.length,tokenSha256:hash(profile.capturedToken),
  tokenKeySha256:hash(secrets.aes.token.keyHex),captchaSecretSha256:hash(secrets.signingSecret),
  fieldCount:profile.token.fields.length,clockIndex:profile.token.clockIndex,traceIndex:profile.token.traceIndex,parity,
  browser:{bootstrap:{http:initial.status,code:initial.response?.Code,success:initial.response?.Success},
    refresh:{http:refresh.status,code:refresh.response?.Code,success:refresh.response?.Success,certifyIdSha256:hash(profile.capturedCertifyId),endpoint:refresh.url}},local};
const out=path.join(root,'analysis/initcaptcha/samples',batch,'manifest.json');
const manifest=fs.existsSync(out)?JSON.parse(fs.readFileSync(out,'utf8')):{batchId:batch,targetCount:15,samples:[]};
manifest.samples=manifest.samples.filter(s=>s.id!==id).concat(record).sort((a,b)=>a.id.localeCompare(b.id));
fs.writeFileSync(out,JSON.stringify(manifest,null,2));
console.log(JSON.stringify({id,count:manifest.samples.length,locale:record.environment.language,screen:record.environment.screen,
  tokenCharacters:record.tokenCharacters,parity,browser:record.browser.refresh.code+'/'+record.browser.refresh.success,
  local:local.code+'/'+local.success}));
