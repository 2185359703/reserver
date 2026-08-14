// 密钥运行时提取探针:
// 1) 注入 VM 主函数, 记录加密函数 (单字符串参数) 调用的捕获数组 -> 密钥数组
// 2) 抓取 4 段明文-密文对, 用于 Python 端确定偏移映射
// 输出: JSON {key_candidates, enc_pairs}
"use strict";
const fs = require("fs");
const vm = require("vm");

const FIXED = Date.now();

function makeCtx() {
  const ctx = {};
  ctx.window = ctx; ctx.self = ctx; ctx.top = ctx; ctx.parent = ctx; ctx.globalThis = ctx;
  ctx.__RESULT = { keys: [], pairs: [] };
  ctx.navigator = { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", platform: "Win32", language: "zh-CN", hardwareConcurrency: 8, maxTouchPoints: 0, webdriver: false, cookieEnabled: true, onLine: true, plugins: [], mimeTypes: [], languages: ["zh-CN"], vendor: "Google Inc." };
  ctx.screen = { width: 1920, height: 1080, colorDepth: 24, pixelDepth: 24 };
  ctx.location = { href: "https://turing.captcha.gtimg.com/1/template/drag_ele.html", protocol: "https:", host: "turing.captcha.gtimg.com", hostname: "turing.captcha.gtimg.com", origin: "https://turing.captcha.gtimg.com", pathname: "/1/template/drag_ele.html", hash: "", search: "" };
  ctx.document = { readyState: "complete", cookie: "", referrer: "", title: "", createElement: () => ({ style:{}, setAttribute(){}, getAttribute:()=>null, addEventListener(){}, appendChild(){}, getContext: () => null, toDataURL: () => "data:image/png;base64,AAAA" }), getElementById: () => null, getElementsByTagName: () => [], querySelector: () => null, querySelectorAll: () => [], addEventListener(){}, createTextNode: (t)=>({nodeType:3,textContent:t}), documentElement: {}, head: {}, body: {} };
  ctx.performance = { now: () => 123456789.123, timing: {}, navigation: {}, getEntries: () => [], getEntriesByType: () => [] };
  ctx.crypto = { getRandomValues: (arr) => { for (let i=0;i<arr.length;i++) arr[i] = (i * 7 + 3) % 256; return arr; }, subtle: { digest: () => Promise.resolve(new ArrayBuffer(32)), encrypt: () => Promise.resolve(new ArrayBuffer(16)), decrypt: () => Promise.resolve(new ArrayBuffer(16)) } };
  ctx.atob = (s) => Buffer.from(s, "base64").toString("binary");
  ctx.btoa = (s) => Buffer.from(s, "binary").toString("base64");
  ctx.TextEncoder = require("util").TextEncoder;
  ctx.TextDecoder = require("util").TextDecoder;
  ctx.Blob = function(p){ this.size = p ? p.length : 0; };
  ctx.URL = URL; ctx.URLSearchParams = URLSearchParams;
  ctx.setTimeout = setTimeout; ctx.clearTimeout = clearTimeout;
  ctx.setInterval = setInterval; ctx.clearInterval = clearInterval;
  ctx.Date = Date; ctx.Date.now = () => FIXED;
  ctx.Date.parse = Date.parse; ctx.Date.UTC = Date.UTC;
  ctx.Math = Object.create(Math);
  ctx.Math.random = () => 0.123456789;
  ctx.JSON = JSON; ctx.parseInt = parseInt; ctx.parseFloat = parseFloat;
  ctx.isNaN = isNaN; ctx.isFinite = isFinite; ctx.decodeURIComponent = decodeURIComponent;
  ctx.encodeURIComponent = encodeURIComponent; ctx.String = String; ctx.Number = Number;
  ctx.Boolean = Boolean; ctx.Array = Array; ctx.Object = Object; ctx.RegExp = RegExp;
  ctx.Error = Error; ctx.TypeError = TypeError; ctx.Promise = Promise; ctx.Int32Array = Int32Array;
  ctx.Uint8Array = Uint8Array; ctx.Uint8ClampedArray = Uint8ClampedArray; ctx.Float32Array = Float32Array;
  ctx.ArrayBuffer = ArrayBuffer; ctx.DataView = DataView; ctx.Symbol = Symbol;
  ctx.XMLHttpRequest = function(){ return { open(){}, send(){}, setRequestHeader(){}, getResponseHeader:()=>null, getAllResponseHeaders:()=>"", abort(){}, addEventListener(){}, readyState:0, status:0, responseText:"", response:"" }; };
  ctx.Worker = function(){ return { postMessage(){}, terminate(){}, addEventListener(){}, onmessage:null, onerror:null }; };
  ctx.addEventListener = function(){}; ctx.removeEventListener = function(){};
  ctx.dispatchEvent = () => true;
  ctx.postMessage = function(){};
  ctx.Image = function(){ return { addEventListener(){}, set src(v){ if(this.onload) setTimeout(this.onload, 0); }, get src(){ return ""; } }; };
  return ctx;
}

const tdcFile = process.argv[2];
if (!tdcFile) { console.error("usage: node _key_extract_probe.js <tdc.js>"); process.exit(1); }
const ctx = makeCtx();
let code = fs.readFileSync(tdcFile, "utf-8");

// 1) VM 主函数签名
const m = code.match(/function __TENCENT_CHAOS_VM\((\w+),(\w+),(\w+),(\w+),(\w+),(\w+),(\w+),(\w+)/);
if (!m) { console.error("NO VM SIG"); process.exit(1); }
const CAP = m[4];
const FID = m[1];

// 2) 主循环 (通用正则)
const lm = code.match(/for\(var (\w+)=!1;!\1;\)\1=(\w+)\[(\w+)\[(\w+)\+\+\]\]\(\)/);
if (!lm) { console.error("NO MAINLOOP"); process.exit(1); }
const IP = lm[4];
const BCR = lm[3];

// 3) 主循环注入: 记录指令流 (用于捕获加密调用的返回)
const old = `for(var ${lm[1]}=!1;!${lm[1]};)${lm[1]}=${lm[2]}[${lm[3]}[${lm[4]}++]]()`;
const new_ = `for(var ${lm[1]}=!1;!${lm[1]};){var __op=${lm[3]}[${lm[4]}++];${lm[1]}=${lm[2]}[__op]()}`;
code = code.replace(old, new_);

// 4) VM 主函数入口注入: 加密函数特征 = 捕获数组包含 s: 前缀长字符串
const braceIdx = code.indexOf("{", code.indexOf(m[0]));
const entryProbe = [
  "try{var __c4=" + CAP + ";",
  "if(__c4&&__c4.length>3&&window.__RESULT){",
  // 找捕获数组中的长字符串明文 (无 s: 前缀, 纯字符串)
  "var __pt=null,__keys=[];",
  "for(var __i=0;__i<__c4.length;__i++){",
  "var __x=__c4[__i];",
  "var __sval=null;",
  "if(typeof __x==='string'&&__x.length>15){__sval=__x;}",
  "if(Array.isArray(__x)&&__x.length===1&&typeof __x[0]==='string'&&__x[0].length>15){__sval=__x[0];}",
  "if(__sval&&!__pt&&(__sval.charAt(0)==='['||__sval.charAt(0)==='{'||__sval.charAt(0)=='\"'||__sval.charAt(0)==='s')){__pt=__sval;}",
  "if(Array.isArray(__x)&&__x.length>=1&&Array.isArray(__x[0])&&__x[0].length===4&&typeof __x[0][0]==='number'&&__x[0][0]>1e8&&__x[0][0]<2147483648){",
  "var __kk=__x[0];if(!window.__RESULT.keys.some(function(z){return z.join(',')===__kk.join(',')})){window.__RESULT.keys.push(__kk.slice(0));}",
  "}",
  "}",
  "if(__pt&&!window.__RESULT.pairs.some(function(p){return p[0]===__pt})){window.__RESULT.pairs.push([__pt,null]);}",
  "}",
  "}catch(__e){}",
].join("");
code = code.slice(0, braceIdx + 1) + entryProbe + code.slice(braceIdx + 1);

// 5) 闭包调用点注入: 记录加密函数返回值 (密文) + 捕获加密函数压入的偏移常量
const cm = code.match(/return __TENCENT_CHAOS_VM\(([\w,]+)\)\}\)\}/);
if (cm) {
  const argsList = cm[1];
  const fidArg = argsList.split(",")[0];
  const rep =
    "var __tr=__TENCENT_CHAOS_VM(" + argsList + ");" +
    "if(window.__RESULT&&window.__RESULT.pairs&&window.__RESULT.pairs.length){try{" +
    "var __pt=null;var __ca=arguments&&arguments[0];" +
    "if(typeof __ca==='string'&&__ca.length>15){__pt=__ca;}" +
    "if(__pt){for(var __pi=0;__pi<window.__RESULT.pairs.length;__pi++){if(window.__RESULT.pairs[__pi][0]===__pt&&window.__RESULT.pairs[__pi][1]===null){window.__RESULT.pairs[__pi][1]=(typeof __tr==='string')?__tr:String(__tr);}}}" +
    "}catch(__e){}}" +
    "return __tr})}";
  code = code.replace(cm[0], rep);
}

// 6) hook push const: 捕获加密函数执行时压入的大整数 (偏移常量候选)
// opcode: function(){STK.push(BCR[IP++])}  → 记录压入的 1e5-1e7 整数
const stkName = m[4];
const pushRe = new RegExp("function\\(\\)\\{" + stkName + "\\.push\\(" + BCR + "\\[\\w\\+\\+\\]\\)\\}");
const pushMatch = code.match(pushRe);
if (pushMatch) {
  const rep2 = "function(){var __c=" + BCR + "[" + IP + "++];if(window.__RESULT&&typeof __c==='number'&&Math.abs(__c)>1e5&&Math.abs(__c)<1e7){if(window.__RESULT.consts===undefined){window.__RESULT.consts=[]}window.__RESULT.consts.push(__c)}" + stkName + ".push(__c)}";
  code = code.replace(pushMatch[0], rep2);
  console.error("push-const hooked");
} else {
  console.error("push-const NOT hooked");
}

vm.createContext(ctx);
try {
  vm.runInContext(code, ctx, { filename: "tdc.js", timeout: 30000 });
  ctx.TDC.setData({ ft: FIXED });
  ctx.TDC.getData(true);
  const R = ctx.__RESULT;
  // 输出 JSON (只含可序列化数据)
  console.log(JSON.stringify({
    keys: R.keys,
    pairs: R.pairs.filter(p => p[1] !== null),
    consts: R.consts || [],
  }));
} catch (e) {
  console.error("RUN FAIL:", e.message.slice(0, 300));
  process.exit(1);
}
