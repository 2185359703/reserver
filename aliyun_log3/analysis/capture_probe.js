// Insert after FeiLin's rD initialization inside its original module scope.
// Used only during Camoufox sampling; raw material is saved under .private.
window.__ali_sample = {
  secret: rD, config: rv, aes: [], requests: [],
};
(function () {
  const a = window.__ali_sample;
  const c = window.__ALIYUN_CRYPT;
  for (const method of ['encrypt', 'decrypt']) {
    const original = c.AES[method];
    c.AES[method] = function (message, key, options) {
      const result = original.apply(this, arguments);
      try {
        if (a.aes.length < 150) a.aes.push({
          method,
          message: typeof message === 'string' ? message : message.toString(c.enc.Utf8),
          key: typeof key === 'string' ? key : key.toString(c.enc.Hex),
          keyIsString: typeof key === 'string',
          iv: options && options.iv ? options.iv.toString(c.enc.Hex) : null,
          result: method === 'decrypt' ? result.toString(c.enc.Utf8) : result.toString(),
        });
      } catch (_) {}
      return result;
    };
  }
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__ali_sample_request = {method, url: String(url), headers: {}};
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (this.__ali_sample_request) this.__ali_sample_request.headers[name] = value;
    return originalHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__ali_sample_request;
    if (meta && meta.url.includes('cloudauth-device') && typeof body === 'string' && body.includes('AaduaneId=')) {
      const record = {...meta, body, startedAt: Date.now(), stack: new Error().stack};
      a.requests.push(record);
      this.addEventListener('loadend', () => {
        record.status = this.status;
        record.finishedAt = Date.now();
        try { record.response = JSON.parse(this.responseText); } catch (_) {}
      }, {once: true});
    }
    return originalSend.apply(this, arguments);
  };
})();
