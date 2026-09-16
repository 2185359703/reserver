// 对比探针 collect 与 4 段拼接，确定组装顺序
"use strict";
const fs = require("fs");
const vm = require("vm");

function makeCtx() {
  const ctx = {};
  ctx.window = ctx; ctx.self = ctx; ctx.top = ctx; ctx.parent = ctx; ctx.globalThis = ctx;
  ctx.__FN = [];
  ctx.navigator = { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36", platform: "Win32", language: "zh-CN", hardwareConcurrency: 8, maxTouchPoints: 0, webdriver: false, cookieEnabled: true, onLine: true, plugins: [], mimeTypes: [], languages: ["zh-CN"], vendor: "Google Inc." };
  ctx.screen = { width: 1920, height: 1080, colorDepth: 24, pixelDepth: 24 };
  ctx.location = { href: "https://turing.captcha.gtimg.com/1/template/drag_ele.html", protocol: "https:", host: "turing.captcha.gtimg.com", hostname: "turing.captcha.gtimg.com", origin: "https://turing.captcha.gtimg.com", pathname: "/1/template/drag_ele.html", hash: "", search: "" };
  ctx.document = { readyState: "complete", cookie: "", referrer: "", title: "", createElement: () => ({ style:{}, setAttribute(){}, getAttribute:()=>null, addEventListener(){}, appendChild(){}, getContext: () => null, toDataURL: () => "data:image/png;base64,AAAA" }), getElementById: () => null, getElementsByTagName: () => [], querySelector: () => null, querySelectorAll: () => [], addEventListener(){}, createTextNode: (t)=>({nodeType:3,textContent:t}), documentElement: {}, head: {}, body: {} };
  ctx.performance = { now: () => 0, timing: {}, navigation: {}, getEntries: () => [], getEntriesByType: () => [] };
  ctx.crypto = { getRandomValues: (arr) => { for (let i=0;i<arr.length;i++) arr[i] = (i * 7) % 256; return arr; }, subtle: { digest: () => Promise.resolve(new ArrayBuffer(32)), encrypt: () => Promise.resolve(new ArrayBuffer(16)), decrypt: () => Promise.resolve(new ArrayBuffer(16)) } };
  ctx.atob = (s) => Buffer.from(s, "base64").toString("binary");
  ctx.btoa = (s) => Buffer.from(s, "binary").toString("base64");
  ctx.TextEncoder = require("util").TextEncoder;
  ctx.TextDecoder = require("util").TextDecoder;
  ctx.Blob = function(p){ this.size = p ? p.length : 0; };
  ctx.URL = URL; ctx.URLSearchParams = URLSearchParams;
  ctx.setTimeout = setTimeout; ctx.clearTimeout = clearTimeout;
  ctx.setInterval = setInterval; ctx.clearInterval = clearInterval;
  const FIXED = 1750000000000;
  ctx.Date = function(...a){ return a.length ? new (Function.prototype.bind.apply(Date, [null].concat(a)))() : new Date(FIXED); };
  ctx.Date.now = () => FIXED;
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

const tdcFile = process.argv[2] || "./_tdc_sample.js";
const code = fs.readFileSync(tdcFile, "utf-8");
const ctx = makeCtx();
vm.createContext(ctx);
vm.runInContext(code, ctx, { filename: "tdc.js", timeout: 20000 });
ctx.TDC.setData({ ft: 1750000000000 });
const collect = ctx.TDC.getData(true);
console.log("collect:", collect);
console.log("collect len:", collect.length);

// 检查 collect 是否是 URL 编码的 base64
console.log("\ndecoded?", decodeURIComponent(collect).slice(0, 80));
