'use strict';

// rs-reverse 的常量表生成器沿用原挑战变量名并在非 strict 模块中写入它。
// 单文件打包后模块共享 strict 作用域，因此提前建立同名全局绑定。
globalThis._$iv = 0;

const https = require('https');
const path = require('path');
const moduleAlias = require('module-alias');

const TARGET_URL = 'https://qikan.cqvip.com/Qikan/Journal/Summary?kind=1&gch=94178X';
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0';
const S_NAME = '6HZbKHDjIEcgS';
const T_NAME = '6HZbKHDjIEcgT';
const TRACE = process.argv.includes('--trace');

function trace(step, message) {
  if (TRACE) console.error(`[${step}] ${message}`);
}

const rsRoot = path.dirname(require.resolve('rs-reverse/package.json'));
moduleAlias(rsRoot);

const gv = require(path.join(rsRoot, 'src', 'handler', 'globalVarible'));
const Coder = require(path.join(rsRoot, 'src', 'handler', 'Coder'));
const parser = require(path.join(rsRoot, 'src', 'handler', 'parser'));
const getImmucfg = require(path.join(rsRoot, 'utils', 'getImmucfg'));
const { simpleEncrypt } = require(path.join(rsRoot, 'utils', 'simpleCrypt'));

const {
  ascii2string,
  combine4,
  decrypt,
  fixedValue20,
  numarrAddTime,
  numarrEncrypt,
  numarrJoin,
  numToNumarr2,
  numToNumarr4,
  numToNumarr8,
  string2ascii,
  uuid,
  xor,
} = parser;

// rs-reverse 1.16.3 的通用 VM/密钥恢复可以直接复用；下面三个函数放在
// 本项目内，是因为当前维普分支需要“4 个独立 IV 随机值”和正确的尾字节编码。
function encode(cfg, val, idx, cfgnum) {
  const list = cfg[idx];
  const arr = [0, 0, 0, 0];
  let one = val[0] ^ list[0];
  let two = val[idx ? 3 : 1] ^ list[1];
  let three = val[2] ^ list[2];
  let four = val[idx ? 1 : 3] ^ list[3];
  let cursor = 4;
  for (let i = 0; i < list.length / 4 - 2; i++) {
    const none = cfgnum[0][one >>> 24] ^ cfgnum[1][two >> 16 & 255]
      ^ cfgnum[2][three >> 8 & 255] ^ cfgnum[3][four & 255] ^ list[cursor];
    const ntwo = cfgnum[0][two >>> 24] ^ cfgnum[1][three >> 16 & 255]
      ^ cfgnum[2][four >> 8 & 255] ^ cfgnum[3][one & 255] ^ list[cursor + 1];
    const nthree = cfgnum[0][three >>> 24] ^ cfgnum[1][four >> 16 & 255]
      ^ cfgnum[2][one >> 8 & 255] ^ cfgnum[3][two & 255] ^ list[cursor + 2];
    four = cfgnum[0][four >>> 24] ^ cfgnum[1][one >> 16 & 255]
      ^ cfgnum[2][two >> 8 & 255] ^ cfgnum[3][three & 255] ^ list[cursor + 3];
    cursor += 4;
    [one, two, three] = [none, ntwo, nthree];
  }
  for (let i = 0; i < 4; i++) {
    arr[idx ? 3 & -i : i] = cfgnum[4][one >>> 24] << 24
      ^ cfgnum[4][two >> 16 & 255] << 16
      ^ cfgnum[4][three >> 8 & 255] << 8
      ^ cfgnum[4][four & 255] ^ list[cursor++];
    [one, two, three, four] = [two, three, four, one];
  }
  return arr;
}

function getCfg(numarr) {
  const ret = combine4(numarr.length % 16 !== 0 ? numarrAddTime.reverse(numarr)[0] : numarr);
  const cfgnum04 = gv.cfgnum[0][4];
  const len = ret.length;
  const arr = [];
  let i;
  let j;
  let temp;
  for (i = len, j = 1; i < 4 * len + 28; i++) {
    temp = ret[i - 1];
    if (i % len === 0 || len === 8 && i % len === 4) {
      temp = cfgnum04[temp >>> 24] << 24 ^ cfgnum04[temp >> 16 & 255] << 16
        ^ cfgnum04[temp >> 8 & 255] << 8 ^ cfgnum04[temp & 255];
      if (i % len === 0) {
        temp = temp << 8 ^ temp >>> 24 ^ j << 24;
        j = j << 1 ^ (j >> 7) * 283;
      }
    }
    ret[i] = ret[i - len] ^ temp;
  }
  for (j = 0; i; j++, i--) {
    temp = ret[j & 3 ? i : i - 4];
    arr[j] = i <= 4 || j < 4
      ? temp
      : gv.cfgnum[1][0][cfgnum04[temp >>> 24]]
        ^ gv.cfgnum[1][1][cfgnum04[temp >> 16 & 255]]
        ^ gv.cfgnum[1][2][cfgnum04[temp >> 8 & 255]]
        ^ gv.cfgnum[1][3][cfgnum04[temp & 255]];
  }
  return [ret, arr];
}

function encryptMode1(valarr, keyarr, flag = 1, random) {
  const cfg = getCfg(keyarr);
  const max = Math.floor(valarr.length / 16) + 1;
  let ans = [];
  let previous;
  const fill = 16 - valarr.length % 16;
  if (flag) {
    ans = previous = new Array(4).fill(4294967295).map((limit) => {
      const currentRandom = Array.isArray(random) ? random.shift() : random;
      return Math.floor((currentRandom ?? Math.random()) * limit);
    });
  }
  const copyarr = numToNumarr4.reverse_sign([...valarr, ...new Array(fill).fill(fill)]);
  for (let i = 0; i < max;) {
    let current = copyarr.slice(i << 2, ++i << 2);
    if (previous) current = [0, 1, 2, 3].map((index) => current[index] ^ previous[index]);
    previous = encode(cfg, current, 0, gv.cfgnum[0]);
    ans.push(...previous);
  }
  return numToNumarr4(ans);
}

function encryptMode2(valarr, keyarr, flag = 1) {
  const cfg = getCfg(keyarr);
  const output = [];
  let blocks = combine4(valarr);
  let previous = [];
  if (flag) {
    previous = blocks.slice(0, 4);
    blocks = blocks.slice(4);
  }
  for (let i = 0; i < blocks.length / 4;) {
    const next = blocks.slice(i << 2, ++i << 2);
    let value = encode(cfg, next, 1, gv.cfgnum[1]);
    if (previous.length) value = value.map((item, index) => item ^ previous[index]);
    output.push(...value);
    previous = next;
  }
  const bytes = output.flatMap((item) => numToNumarr4(item));
  return bytes.slice(0, bytes.length - bytes[bytes.length - 1]);
}

function numarr2string(numarr, alphabet = gv.basestr.split('')) {
  let index = 0;
  const output = [];
  while (index < numarr.length - 2) {
    const [one, two, three] = [numarr[index], numarr[index + 1], numarr[index + 2]];
    index += 3;
    output.push(
      alphabet[one >> 2],
      alphabet[(one & 3) << 4 | two >> 4],
      alphabet[(two & 15) << 2 | three >> 6],
      alphabet[three & 63],
    );
  }
  if (index < numarr.length) {
    output.push(
      alphabet[numarr[index] >> 2],
      alphabet[(numarr[index] & 3) << 4 | numarr[index + 1] >> 4],
    );
    if (numarr[index + 1] !== undefined) {
      output.push(alphabet[(numarr[index + 1] & 15) << 2]);
    }
  }
  return output.join('');
}

function request(url, headers) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks),
      }));
    });
    req.setTimeout(30000, () => req.destroy(new Error('request timeout')));
    req.on('error', reject);
  });
}

function extractTs(html) {
  const blocks = Array.from(
    html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi),
    (match) => match[1],
  );
  const code = blocks.find((item) => item.includes('$_ts.nsd') && item.includes('$_ts.cd'));
  if (!code) throw new Error('412 页面中没有找到 $_ts');
  const ts = Function('window', `${code}; return $_ts;`)({});
  ts.from = TARGET_URL;
  ts.hasDebug = false;
  return ts;
}

function initGlobalState(challengeCode) {
  const urlArgument = {
    url: TARGET_URL,
    jscode: { code: challengeCode },
    metaContent: [],
  };
  gv._setAttr('argv', { url: urlArgument, mate: urlArgument });
  const match = challengeCode.match(
    /_\$[\$_A-Za-z0-9]{2}=_\$[\$_A-Za-z0-9]{2}\(0,([0-9]+),_\$[\$_A-Za-z0-9]{2}\(/,
  );
  if (!match) throw new Error('挑战 JS 中没有匹配到 keyname 长度');
  const url = new URL(TARGET_URL);
  gv._setAttr('config', {
    offsetConst: {},
    keynameNum: Number(match[1]),
    url,
    hostname: simpleEncrypt(url.hostname.replace(/^www\./, '')),
    immucfg: getImmucfg(challengeCode),
  });
}

function buildCookieT(ts, challengeCode) {
  initGlobalState(challengeCode);
  gv._setAttr('_ts', ts);
  const coder = new Coder({ ...ts, hasCodemap: true, hasDebug: false }, gv.config.immucfg);
  const { codemap } = coder.run();
  gv.config.codemap = codemap;
  parser.init(coder);

  const keys = gv.keys;
  const nowMs = Date.now();
  const runTime = Math.floor(nowMs / 1000);
  const r2mkaTime = +ascii2string(keys[21]);
  const startTime = r2mkaTime - 1;
  const mainFunctionCode = coder.code.slice(...coder.mainFunctionIdx);
  const one = uuid(coder.functionsNameSort[ascii2string(keys[33])].code);
  const sliceLength = Math.floor(mainFunctionCode.length / 100);
  const sliceStart = sliceLength * ascii2string(keys[34]);
  const two = uuid(mainFunctionCode.substr(sliceStart, sliceLength));
  const codeUid = (one ^ two) & 65535;

  const random = () => Math.random();
  random();
  random();
  const samples = Array.from({ length: 98 }, random);
  random();
  const average = samples.reduce((sum, value) => sum + value, 0) / samples.length;
  const variance = samples.reduce((sum, value) => sum + (value - average) ** 2, 0) / samples.length;
  const randomStats = [Math.round(average * 100), Math.round(variance * 100)];

  const browserBlock = numarrJoin(
    0, 0, 37, 135,
    ...numToNumarr4(uuid(USER_AGENT)),
    string2ascii('Win32'),
    ...numToNumarr4(2212),
    ...randomStats,
    0, 0, 0,
    0, 0, 0, 0, 0, 0, 0,
    ...numToNumarr2(998),
    ...numToNumarr2(1920),
    ...numToNumarr2(1055),
    ...numToNumarr2(1920),
    ...numToNumarr8(0),
    ...numToNumarr4(0),
    ...numToNumarr4(0),
    ...numToNumarr4(uuid(new URL(TARGET_URL).pathname.toUpperCase())),
    ...numToNumarr4(3367415333),
  );
  const timeBlock = numarrJoin(
    3, 13,
    ...numToNumarr4(r2mkaTime),
    ...numToNumarr4(+ascii2string(keys[19])),
    ...numToNumarr8(
      Math.floor(random() * 1048575) * 4294967296 + ((nowMs & 4294967295) >>> 0),
    ),
    +ascii2string(keys[24]),
    string2ascii(new URL(TARGET_URL).hostname.substr(0, 20)),
  );
  const codeBlock = [0, 0, 0, 0, 0, 0, 0, 0, 10, 12, ...numToNumarr2(codeUid)];
  const stateBlock = [
    1,
    ...numToNumarr2(0),
    ...numToNumarr2(0),
    0,
    ...encryptMode2(
      decrypt(ascii2string(keys[22])),
      numarrAddTime(keys[16], undefined, random())[0],
    ),
    ...numToNumarr2(0),
  ];
  const basearr = numarrJoin(
    3, browserBlock,
    10, timeBlock,
    7, codeBlock,
    0, [0],
    6, stateBlock,
    2, fixedValue20(),
    9, [0],
    13, [0],
  );
  if (basearr.length !== 148) throw new Error(`basearr 长度异常：${basearr.length}`);

  const compressed = numarrEncrypt(basearr);
  const innerKey = numarrAddTime(keys[17], runTime, random())[0];
  const innerEncrypted = encryptMode1(xor(compressed, keys[2], 16), innerKey, 0);
  const nextarr = numarrJoin(
    numarrJoin(2, numToNumarr4([r2mkaTime, startTime]), keys[2]),
    (innerEncrypted.length >> 8 & 255) | 128,
    innerEncrypted,
  );
  const outerKey = numarrAddTime(keys[16], runTime, random())[0];
  random();
  const outerEncrypted = encryptMode1(
    [...numToNumarr4(uuid(nextarr)), ...nextarr],
    outerKey,
    1,
    [random(), random(), random(), random()],
  );
  return {
    value: `0${numarr2string(outerEncrypted)}`,
    basearrLength: basearr.length,
    randomStats,
    codeUid,
  };
}

async function main() {
  const headers = {
    'User-Agent': USER_AGENT,
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
  };
  const first = await request(TARGET_URL, headers);
  const html = first.body.toString('utf8');
  trace('1/6 challenge', `status=${first.status} bytes=${first.body.length}`);
  if (first.status !== 412 || !html.includes('$_ts')) {
    throw new Error(`预期 412 挑战，实际 status=${first.status}, bytes=${first.body.length}`);
  }
  const setCookie = Array.isArray(first.headers['set-cookie'])
    ? first.headers['set-cookie'].join('; ')
    : String(first.headers['set-cookie'] || '');
  const sMatch = setCookie.match(new RegExp(`${S_NAME}=([^;]+)`));
  const scriptMatch = html.match(/<script[^>]+src=["']([^"']+)/i);
  if (!sMatch || !scriptMatch) throw new Error('挑战响应缺少 S Cookie 或外部 JS');
  const s = sMatch[1];
  trace('2/6 server-cookie', `name=${S_NAME} length=${s.length} value=<redacted>`);
  const scriptUrl = new URL(scriptMatch[1], TARGET_URL).href;
  const script = await request(scriptUrl, {
    'User-Agent': USER_AGENT,
    Accept: '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    Referer: TARGET_URL,
    Cookie: `${S_NAME}=${s}`,
    'Sec-Fetch-Dest': 'script',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'same-origin',
  });
  if (script.status !== 200 || script.body.length < 100000) {
    throw new Error(`挑战 JS 异常：status=${script.status}, bytes=${script.body.length}`);
  }
  trace('3/6 challenge-js', `status=${script.status} bytes=${script.body.length}`);
  const ts = extractTs(html);
  const t = buildCookieT(ts, script.body.toString('utf8'));
  trace(
    '4/6 local-rebuild',
    `nsd=${ts.nsd} basearr=${t.basearrLength} randomStats=${t.randomStats.join(',')} codeUid=${t.codeUid}`,
  );
  trace('5/6 client-cookie', `name=${T_NAME} length=${t.value.length} value=<redacted>`);

  if (process.argv.includes('--json-cookie')) {
    process.stdout.write(JSON.stringify({ s, t: t.value }));
    return;
  }
  const result = {
    challengeStatus: first.status,
    challengeScriptBytes: script.body.length,
    nsd: ts.nsd,
    basearrLength: t.basearrLength,
    randomStats: t.randomStats,
    codeUid: t.codeUid,
    sLength: s.length,
    tLength: t.value.length,
  };
  if (process.argv.includes('--verify')) {
    const verifyHeaders = {
      ...headers,
      Referer: TARGET_URL,
      Cookie: `${S_NAME}=${s}; ${T_NAME}=${t.value}`,
      'Sec-Fetch-Site': 'same-origin',
    };
    delete verifyHeaders['Sec-Fetch-User'];
    const replay = await request(TARGET_URL, verifyHeaders);
    const text = replay.body.toString('utf8');
    result.verifyStatus = replay.status;
    result.verifyBytes = replay.body.length;
    result.verifyHasJournal = text.includes('中国农村经济');
    result.verifyPassed = replay.status === 200 && replay.body.length > 100000
      && result.verifyHasJournal;
    trace(
      '6/6 replay',
      `status=${replay.status} bytes=${replay.body.length} journal=${result.verifyHasJournal} passed=${result.verifyPassed}`,
    );
  }
  if (process.argv.includes('--show-cookie')) {
    result.cookie = `${S_NAME}=${s}; ${T_NAME}=${t.value}`;
  }
  console.log(JSON.stringify(result, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
