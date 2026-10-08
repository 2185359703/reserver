'use strict';
const fs=require('node:fs');const path=require('node:path');const {createHash}=require('node:crypto');
const {extractProfile}=require('./extract_profile.cjs');const {buildRequest,useServerConfig}=require('../../aliyun_initcaptcha_node.cjs');
const root=path.join(__dirname,'../..');const batch=process.argv[2];
if(!/^\d{8}-[a-f0-9]{8}$/.test(batch))throw new Error('批次编号格式错误');
const base=path.join(root,'analysis/initcaptcha/samples',batch);const out=path.join(base,'manifest.json');
const manifest=JSON.parse(fs.readFileSync(out,'utf8'));if(manifest.samples.length!==15)throw new Error('有效样本不足 15 组');
const index={schemaVersion:1,batchId:batch,samples:[]};const hash=value=>createHash('sha256').update(value).digest('hex');
for(const row of manifest.samples){
  const dir=path.join(root,'.private/initcaptcha/cohort',batch,row.id);
  const capture=JSON.parse(fs.readFileSync(path.join(dir,'capture.json'),'utf8'));
  const {profile,secrets,initial,refresh}=extractProfile(capture,capture.headers,{sampleId:row.id,batchId:batch});
  fs.writeFileSync(path.join(dir,'profile.json'),JSON.stringify(profile,null,2));
  fs.writeFileSync(path.join(dir,'secrets.json'),JSON.stringify(secrets,null,2));
  for(const result of [row.browser.bootstrap,row.browser.refresh])if(result.http!==200||result.code!=='Success'||result.success!==true)throw new Error('浏览器请求未成功');
  if(row.local.http!==200||row.local.code!=='Success'||row.local.success!==true)throw new Error('本地请求未成功');
  if(!row.local.freshServerSession||!row.local.freshServerFields||!row.local.freshServerCertifyId)throw new Error('服务端状态配对不正确');
  const fixed=buildRequest(profile,secrets,{fixed:true,certifyId:profile.capturedCertifyId,timestamp:profile.capturedTimestamp,nonce:profile.capturedNonce});
  if(fixed.body!==refresh.body)throw new Error('完整刷新请求比对失败');
  const updated=useServerConfig(profile,secrets,initial.response.DeviceConfig);
  if(updated.profile.token.sessionId!==profile.token.sessionId||updated.secrets.aes.token.keyHex!==secrets.aes.token.keyHex)throw new Error('DeviceConfig 解包与浏览器不一致');
  row.browser.refresh.endpoint=refresh.url;row.environmentSha256=hash(JSON.stringify(row.environment));
  index.samples.push({id:row.id,profile:path.relative(root,path.join(dir,'profile.json')).replaceAll('\\','/'),secrets:path.relative(root,path.join(dir,'secrets.json')).replaceAll('\\','/')});
}
const unique=fn=>new Set(manifest.samples.map(fn)).size;
const frequencies=fn=>manifest.samples.reduce((o,r)=>{const value=fn(r);o[value]=(o[value]||0)+1;return o;},{});
manifest.summary={groups:15,fullWireParity:15,browserBootstrapSuccess:15,browserRefreshSuccess:15,localFlowSuccess:15,
  freshServerContextVerified:15,locales:frequencies(r=>r.environment.language),screens:frequencies(r=>`${r.environment.screen.width}x${r.environment.screen.height}`),
  distinctEnvironments:unique(r=>r.environmentSha256),distinctCapturedSessions:unique(r=>r.sessionSha256),distinctCapturedTokens:unique(r=>r.tokenSha256),
  distinctLocalTokens:unique(r=>r.local.tokenSha256),distinctLocalCertifyIds:unique(r=>r.local.certifyIdSha256),
  distinctCaptchaSigningKeys:unique(r=>r.captchaSecretSha256),distinctCapturedTokenKeys:unique(r=>r.tokenKeySha256),
  observedRefreshEndpoints:frequencies(r=>r.browser.refresh.endpoint)};
for(const key of ['distinctCapturedSessions','distinctCapturedTokens','distinctLocalTokens','distinctLocalCertifyIds'])if(manifest.summary[key]!==15)throw new Error('样本存在重复');
fs.writeFileSync(out,JSON.stringify(manifest,null,2));
fs.writeFileSync(path.join(root,'.private/initcaptcha/index.json'),JSON.stringify(index,null,2),{mode:0o600});
console.log(JSON.stringify(manifest.summary,null,2));
