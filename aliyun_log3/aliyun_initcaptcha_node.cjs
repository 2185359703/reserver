'use strict';
// 只生成初始化/刷新参数，不处理验证码答案或业务验证结果。
const fs=require('node:fs');
const path=require('node:path');
const {createHash,randomInt}=require('node:crypto');
const {parseArgs}=require('node:util');
const codec=require('./aliyun_codec.cjs');

function timestampUTC(value=Date.now()) {
  return new Date(value).toISOString().replace(/\.\d{3}Z$/,'Z');
}
function encrypt(secrets,role,text) {
  const {keyHex,ivHex}=secrets.aes[role];return codec.encrypt(String(text),keyHex,ivHex);
}
function buildDeviceData(profile,secrets) {
  const b=profile.bootstrap;
  return encrypt(secrets,'bootstrap',[b.appKey,b.deviceType,encrypt(secrets,'flag',b.flag),b.appVersion,b.environment,b.extra].join('#'));
}
function buildDeviceToken(profile,secrets,options={}) {
  const t=profile.token;const fields=[...t.fields];
  if(!options.fixed){
    const now=options.now??Date.now();fields[t.clockIndex]=String(now);
    const elapsed=now-t.traceOrigin;
    fields[t.traceIndex]=fields[t.traceIndex].split('|').map(item=>{
      if(item.startsWith('93-'))return `93-${elapsed}`;
      if(item.startsWith('94-'))return `94-${elapsed}`;
      return item;
    }).join('|');
  }
  const ciphertext=encrypt(secrets,'token',fields.join('#'));
  const components=[t.prefix,t.sessionId,ciphertext,t.cost];
  const checksum=createHash('md5').update([...components,secrets.tokenMd5Salt].join('#'),'utf8').digest('hex');
  return Buffer.from([...components,checksum].join('#'),'utf8').toString('base64');
}
function useServerConfig(profile,secrets,deviceConfig) {
  const unpack=(role,value)=>{const p=secrets.aes[role];return codec.decrypt(value,p.keyHex,p.ivHex);};
  const parts=unpack('deviceConfig',deviceConfig).split('#');
  if(parts.length!==profile.serverConfig.partCount)throw new Error('新的 DeviceConfig 结构不匹配取样布局');
  const tokenKey=Buffer.from(parts[profile.serverConfig.tokenSecretIndex],'base64');
  if(tokenKey.length!==16)throw new Error('服务端设备令牌 AES 密钥长度异常');
  const fields=[...profile.token.fields];
  for(const [index,position] of Object.entries(profile.serverConfig.fieldMap))fields[Number(index)]=parts[position];
  const sessionId=parts[profile.serverConfig.sessionIndex];
  if(!sessionId)throw new Error('DeviceConfig 中缺少服务端设备会话标识');
  return {profile:{...profile,token:{...profile.token,sessionId,fields}},
    secrets:{...secrets,aes:{...secrets.aes,token:{...secrets.aes.token,keyHex:tokenKey.toString('hex')}}}};
}
function buildRequest(profile,secrets,options={}) {
  const phase=options.phase??'refresh';
  const base=phase==='bootstrap'?profile.bootstrapFields:profile.fields;
  const fields={AaduaneId:base.AaduaneId,SignatureMethod:base.SignatureMethod,SignatureVersion:base.SignatureVersion,
    Format:base.Format,Timestamp:options.timestamp??timestampUTC(options.now),Version:base.Version,Action:'InitCaptcha',SceneId:base.SceneId};
  if(phase==='bootstrap') {
    for(const key of ['Language','Mode'])if(key in base)fields[key]=base[key];
    fields.DeviceData=buildDeviceData(profile,secrets);
  }else{
    if(!options.certifyId)throw new Error('刷新需要服务端初始化响应中的 CertifyId');
    fields.CertifyId=options.certifyId;
    if('Language' in base)fields.Language=base.Language;
    fields.DeviceToken=buildDeviceToken(profile,secrets,options);
  }
  const form=codec.signedForm(fields,secrets.signingSecret,options.nonce);
  return {url:profile.endpoint,headers:{...profile.headers},body:codec.serializeForm(form),form,
    phase,sampleId:profile.sampleId??'baseline',batchId:profile.batchId??null};
}
function loadProfile(options={}) {
  const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
  const baseline=path.join(__dirname,'.private/initcaptcha');
  const sharedKeys=path.join(__dirname,'config/initcaptcha_sdk_keys.json');
  if(options.profile||options.secrets){
    if(!options.profile||!options.secrets)throw new Error('--profile 与 --secrets 必须配套');
    return {profile:read(options.profile),secrets:read(options.secrets)};
  }
  const indexPath=path.join(baseline,'index.json');const sample=options.sample??'random';
  if(sample==='baseline'||!fs.existsSync(indexPath)){
    const privateKeys=path.join(baseline,'secrets.json');
    return {profile:read(path.join(baseline,'profile.json')),secrets:read(fs.existsSync(privateKeys)?privateKeys:sharedKeys)};
  }
  const index=read(indexPath);const entry=sample==='random'?index.samples[randomInt(index.samples.length)]:index.samples.find(s=>s.id===sample);
  if(!entry)throw new Error('未知样本编号');
  const keys=path.resolve(__dirname,entry.secrets);
  return {profile:read(path.resolve(__dirname,entry.profile)),secrets:read(fs.existsSync(keys)?keys:sharedKeys)};
}
function main(){
  const {values}=parseArgs({options:{phase:{type:'string',default:'refresh'},profile:{type:'string'},secrets:{type:'string'},
    sample:{type:'string',default:'random'},'certify-id':{type:'string'},'server-config':{type:'string'},timestamp:{type:'string'},nonce:{type:'string'},fixed:{type:'boolean',default:false},summary:{type:'boolean',default:false}}});
  let pair=loadProfile(values);
  if(values['server-config'])pair=useServerConfig(pair.profile,pair.secrets,JSON.parse(fs.readFileSync(values['server-config'],'utf8')).DeviceConfig);
  const request=buildRequest(pair.profile,pair.secrets,{phase:values.phase,certifyId:values['certify-id'],timestamp:values.timestamp,nonce:values.nonce,fixed:values.fixed});
  const summary={url:request.url,phase:request.phase,sampleId:request.sampleId,bodyBytes:Buffer.byteLength(request.body),timestamp:request.form.Timestamp,
    nonce:request.form.SignatureNonce,deviceTokenCharacters:request.form.DeviceToken?.length,
    deviceTokenSha256:request.form.DeviceToken?createHash('sha256').update(request.form.DeviceToken).digest('hex'):null};
  process.stdout.write(JSON.stringify(values.summary?summary:request));
}
module.exports={timestampUTC,buildDeviceData,buildDeviceToken,buildRequest,useServerConfig,loadProfile};
if(require.main===module){try{main();}catch(e){process.stderr.write(`参数计算失败：${e.message}\n`);process.exitCode=1;}}
