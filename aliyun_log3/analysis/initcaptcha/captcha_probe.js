// 在 AliyunCaptcha.js 的 Ir/Br 配置变量声明之后插入；仅用于受控取样。
window.__init_sample = {
  captchaSecret: Er.KEY_SECRET, captchaKeyId: Er.KEY_ID,
  config: Ir, deviceConfig: Br, sign: Cr, serialize: Qr,
  aes: [], md5: [], requests: [], tokenCalls: [],
};
(function () {
  const a = window.__init_sample;
  const c = window.__ALIYUN_CRYPT;
  for (const method of ['encrypt', 'decrypt']) {
    const original = c.AES[method];
    c.AES[method] = function (message, key, options) {
      const result = original.apply(this, arguments);
      try { if (a.aes.length < 240) a.aes.push({method,
        message: typeof message === 'string' ? message : message.toString(c.enc.Utf8),
        key: typeof key === 'string' ? key : key.toString(c.enc.Hex),
        keyIsString: typeof key === 'string',
        iv: options && options.iv ? options.iv.toString(c.enc.Hex) : null,
        result: method === 'decrypt' ? result.toString(c.enc.Utf8) : result.toString(),
      }); } catch (_) {}
      return result;
    };
  }
  const originalMd5 = c.MD5;
  c.MD5 = function (message) {
    const result = originalMd5.apply(this, arguments);
    try { if (a.md5.length < 100) a.md5.push({
      message: typeof message === 'string' ? message : message.toString(c.enc.Utf8),
      result: result.toString(c.enc.Hex),
    }); } catch (_) {}
    return result;
  };
  const open = XMLHttpRequest.prototype.open;
  const send = XMLHttpRequest.prototype.send;
  const header = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__init_request = {method, url: String(url), headers: {}};
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (this.__init_request) this.__init_request.headers[name] = value;
    return header.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__init_request;
    if (meta && /captcha-open|cloudauth-device/.test(meta.url) && typeof body === 'string' && body.includes('AaduaneId=')) {
      const record = {...meta, body, startedAt: Date.now(), stack: new Error().stack};
      a.requests.push(record);
      this.addEventListener('loadend', () => {
        record.status = this.status; record.finishedAt = Date.now();
        try { record.response = JSON.parse(this.responseText); } catch (_) {}
      }, {once: true});
    }
    return send.apply(this, arguments);
  };
})();
