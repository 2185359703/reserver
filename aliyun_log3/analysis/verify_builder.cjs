'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {buildRequest} = require('../aliyun_log3_node.cjs');
const root = path.join(__dirname, '..');
const read = name => JSON.parse(fs.readFileSync(path.join(root, '.private', name), 'utf8'));
const profile = read('runtime_profile.json');
const capture = read('browser_capture.json');
const request = capture.requests.find(r => new URLSearchParams(r.body).get('Action') === 'Log3');
const expected = Object.fromEntries(new URLSearchParams(request.body));
const calculated = buildRequest(profile, read('secrets.json'), {
  timestamp: profile.capturedTimestamp, nonce: profile.capturedNonce,
});
const checks = {
  data_matches_browser: calculated.form.Data === expected.Data,
  signature_matches_browser: calculated.form.Signature === expected.Signature,
  body_matches_browser: calculated.body === request.body,
};
const next = buildRequest(profile, read('secrets.json'), {timestamp: profile.capturedTimestamp + 1});
checks.new_timestamp_changes_data = next.form.Data !== expected.Data;
checks.new_request_changes_nonce = next.form.SignatureNonce !== expected.SignatureNonce;
checks.new_request_changes_signature = next.form.Signature !== expected.Signature;
console.log(JSON.stringify(checks, null, 2));
if (Object.values(checks).some(v => !v)) process.exitCode = 1;
