'use strict';
// 从真实 AES/MD5/XHR 记录提取初始化与刷新阶段的配套参数。
const fs = require('node:fs');
const path = require('node:path');
function extractProfile(capture, headers = {}, metadata = {}) {
  const forms=capture.requests.filter(r=>new URLSearchParams(r.body).get('Action')==='InitCaptcha');
  const initial=forms.find(r=>new URLSearchParams(r.body).has('DeviceData')&&r.response?.Success===true);
  const refresh=forms.find(r=>new URLSearchParams(r.body).has('CertifyId')&&new URLSearchParams(r.body).has('DeviceToken')&&r.response?.Success===true);
  if(!initial||!refresh)throw new Error('缺少初始化或带 CertifyId 的刷新请求');
  const initialForm=Object.fromEntries(new URLSearchParams(initial.body));
  const refreshForm=Object.fromEntries(new URLSearchParams(refresh.body));
  const aesInput = value => {
    const row=capture.aes.find(e=>e.method==='encrypt'&&e.result===value);
    if(!row||row.keyIsString)throw new Error('未找到原始密钥 AES 入参');
    return row;
  };
  const bootstrap=aesInput(initialForm.DeviceData);
  const bootstrapParts=bootstrap.message.split('#');
  if(bootstrapParts.length!==6)throw new Error('初始化 DeviceData 结构变化');
  const flag=aesInput(bootstrapParts[2]);
  const tokenParts=Buffer.from(refreshForm.DeviceToken,'base64').toString('utf8').split('#');
  if(tokenParts.length!==5||tokenParts[0]!=='WEB')throw new Error('DeviceToken 外层结构变化');
  const token=aesInput(tokenParts[2]);
  const tokenFields=token.message.split('#');
  const md5=capture.md5.find(e=>e.result===tokenParts[4]);
  if(!md5)throw new Error('未捕获 DeviceToken MD5 输入');
  const checksumParts=md5.message.split('#');
  if(checksumParts.length!==5||!checksumParts.slice(0,4).every((p,i)=>p===tokenParts[i]))throw new Error('DeviceToken MD5 结构变化');
  if(tokenFields.length!==142||!/^\d{13}$/.test(tokenFields[74]))throw new Error('设备字段布局变化，需重新观察时钟位置');
  const fields={...refreshForm};delete fields.Timestamp;delete fields.CertifyId;delete fields.DeviceToken;delete fields.SignatureNonce;delete fields.Signature;
  const bootstrapFields={...initialForm};delete bootstrapFields.Timestamp;delete bootstrapFields.DeviceData;delete bootstrapFields.SignatureNonce;delete bootstrapFields.Signature;
  const secrets={signingSecret:capture.captchaSecret,tokenMd5Salt:checksumParts[4],aes:{
    bootstrap:{keyHex:bootstrap.key,ivHex:bootstrap.iv},flag:{keyHex:flag.key,ivHex:flag.iv},token:{keyHex:token.key,ivHex:token.iv},
  }};
  const configEvent=capture.aes.find(e=>e.method==='decrypt'&&e.message===initial.response.DeviceConfig);
  const secretCipher=capture.feilinConfig.secretKey;
  const keyEvent=capture.aes.find(e=>e.method==='decrypt'&&e.message===secretCipher&&Buffer.from(e.result,'utf8').toString('hex')===token.key);
  if(!configEvent||!keyEvent)throw new Error('未捕获服务端 DeviceConfig 的密钥解包过程');
  const configParts=configEvent.result.split('#');
  const secretIndex=configParts.findIndex(p=>Buffer.from(p,'base64').toString('hex')===token.key);
  if(secretIndex<0)throw new Error('服务端原始密钥在 DeviceConfig 中的位置变化');
  secrets.aes.deviceConfig={keyHex:configEvent.key,ivHex:configEvent.iv};
  const profile={schemaVersion:1,...metadata,endpoint:initial.url,observedRefreshEndpoint:refresh.url,headers,fields,bootstrapFields,
    bootstrap:{appKey:bootstrapParts[0],deviceType:bootstrapParts[1],flag:flag.message,appVersion:bootstrapParts[3],environment:bootstrapParts[4],extra:bootstrapParts[5]},
    token:{prefix:tokenParts[0],sessionId:tokenParts[1],fields:tokenFields,cost:tokenParts[3],clockIndex:74,traceIndex:43,
      traceOrigin:capture.feilinConfig.initTime},
    serverConfig:{partCount:configParts.length,tokenSecretIndex:secretIndex,encoding:'base64',
      sessionIndex:configParts.indexOf(tokenParts[1]),
      fieldMap:Object.fromEntries(tokenFields.map((value,index)=>[index,configParts.indexOf(value)])
        .filter(([index,position])=>position>=0&&tokenFields[index].length>8))},
    capturedToken:refreshForm.DeviceToken,capturedTimestamp:refreshForm.Timestamp,capturedNonce:refreshForm.SignatureNonce,
    capturedCertifyId:refreshForm.CertifyId,expectedRefreshBody:refresh.body,capturedBootstrapTimestamp:initialForm.Timestamp,
    capturedBootstrapNonce:initialForm.SignatureNonce,expectedBootstrapBody:initial.body,feilinSdk:capture.feilinSdk,
  };
  return {profile,secrets,initial,refresh};
}
module.exports={extractProfile};
if(require.main===module){
  const root=path.join(__dirname,'../..');const dir=path.join(root,'.private/initcaptcha');
  const capture=JSON.parse(fs.readFileSync(path.join(dir,'baseline_capture.json'),'utf8'));
  const network=JSON.parse(fs.readFileSync(path.join(dir,'baseline_network.json'),'utf8'));
  const {profile,secrets}=extractProfile(capture,network.request_headers,{sampleId:'baseline'});
  fs.writeFileSync(path.join(dir,'profile.json'),JSON.stringify(profile,null,2),{mode:0o600});
  fs.writeFileSync(path.join(dir,'secrets.json'),JSON.stringify(secrets,null,2),{mode:0o600});
  console.log(JSON.stringify({saved:true,fieldCount:profile.token.fields.length,clockIndex:74,traceIndex:43}));
}
