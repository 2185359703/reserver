'use strict';
const fs=require('node:fs');const path=require('node:path');const codec=require('../../aliyun_codec.cjs');
const {buildRequest}=require('../../aliyun_initcaptcha_node.cjs');
const dir=path.join(__dirname,'../../.private/initcaptcha');
const profile=JSON.parse(fs.readFileSync(path.join(dir,'profile.json'),'utf8'));
const secrets=JSON.parse(fs.readFileSync(path.join(dir,'secrets.json'),'utf8'));
const original=JSON.parse(fs.readFileSync(path.join(dir,'original_form.json'),'utf8'));
const bootstrap=buildRequest(profile,secrets,{phase:'bootstrap',timestamp:profile.capturedBootstrapTimestamp,nonce:profile.capturedBootstrapNonce});
const refresh=buildRequest(profile,secrets,{phase:'refresh',fixed:true,certifyId:profile.capturedCertifyId,timestamp:profile.capturedTimestamp,nonce:profile.capturedNonce});
const checks={bootstrapBodyMatches:bootstrap.body===profile.expectedBootstrapBody,refreshBodyMatches:refresh.body===profile.expectedRefreshBody,
  deviceTokenMatches:refresh.form.DeviceToken===profile.capturedToken,originalSignatureMatches:codec.signature(original,secrets.signingSecret)===original.Signature};
const decoded=Buffer.from(original.DeviceToken,'base64').toString('utf8').split('#');
const {createHash}=require('node:crypto');
checks.originalTokenChecksumMatches=createHash('md5').update([...decoded.slice(0,4),secrets.tokenMd5Salt].join('#')).digest('hex')===decoded[4];
try{const plaintext=codec.decrypt(decoded[2],secrets.aes.token.keyHex,secrets.aes.token.ivHex);checks.originalTokenFieldCount=plaintext.split('#').length;}
catch{checks.originalTokenDecrypts=false;}
console.log(JSON.stringify(checks,null,2));
fs.writeFileSync(path.join(__dirname,'baseline_validation.json'),JSON.stringify(checks,null,2));
// 历史用户令牌的设备会话 AES 密钥不在 cURL 中；不将其缺失当作新样本构建失败。
if(['bootstrapBodyMatches','refreshBodyMatches','deviceTokenMatches','originalSignatureMatches','originalTokenChecksumMatches'].some(k=>!checks[k]))process.exitCode=1;
