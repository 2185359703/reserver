// 明文模板验证: 固定环境明文 + 时间戳替换 vs 真实明文
// 输出: 固定环境 4 段明文 (ft 固定为 1750000000000)
"use strict";
const fs = require("fs");
const vm = require("vm");

function makeCtx(now) {
  const ctx = {};
  ctx.window = ctx; ctx.self = ctx; ctx.top = ctx; ctx.parent = ctx; ctx.globalThis = ctx;
  ctx.__CALL = [];
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
  ctx.Date = function(...a){ return a.length ? new (Function.prototype.bind.apply(Date, [null].concat(a)))() : new Date(now); };
  ctx.Date.now = () => now;
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

// 注入: 抓 4 段明文 (加密调用, 通过闭包调用点捕获 arguments)
function injectAndRun(file, now) {
  let code = fs.readFileSync(file, "utf-8");
  // 闭包调用点注入: 记录加密函数 (单字符串参数) 的明文与返回
  const cm = code.match(/return __TENCENT_CHAOS_VM\(([\w,]+)\)\}\)\}/);
  if (cm) {
    const argsList = cm[1];
    const rep =
      "var __tr=__TENCENT_CHAOS_VM(" + argsList + ");" +
      "if(window.__SEGS&&arguments&&arguments.length===1&&typeof arguments[0]==='string'&&arguments[0].length>8&&arguments[0].charAt(0)!=='\\x00'){try{var __pt=arguments[0];var __ct=(typeof __tr==='string')?__tr:String(__tr);if(!window.__SEGS.some(function(s){return s[0]===__pt})){window.__SEGS.push([__pt,__ct]);}}catch(__e){}}" +
      "return __tr})}";
    code = code.replace(cm[0], rep);
  }
  const ctx = makeCtx(now);
  ctx.__SEGS = [];
  vm.createContext(ctx);
  vm.runInContext(code, ctx, { filename: "tdc.js", timeout: 20000 });
  ctx.TDC.setData({ ft: now });
  ctx.TDC.getData(true);
  return ctx.__SEGS;
}

const f = process.argv[2] || "_tdc_v1.js";
// 固定环境明文 (ft=1750000000000)
const fixed = injectAndRun(f, 1750000000000);
console.log(JSON.stringify(fixed));
