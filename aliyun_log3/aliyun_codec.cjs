'use strict';

// Algorithms observed in FeiLin 1.5.1: r0/r2/r9 and CryptoJS AES defaults.
// Keys are supplied at runtime and are never embedded in this module.
const { createCipheriv, createDecipheriv, createHmac, randomUUID } = require('node:crypto');

function percentEncode(value) {
  if (value == null) return null;
  return encodeURIComponent(String(value))
    .replace('+', '%20').replace('*', '%2A').replace('%7E', '~');
}

function canonicalQuery(form) {
  return Object.keys(form).filter(k => k !== 'Signature').sort()
    .map(k => `${percentEncode(k)}=${percentEncode(form[k])}`).join('&');
}

function signature(form, secret) {
  const text = `POST&${percentEncode('/')}&${percentEncode(canonicalQuery(form))}`;
  return createHmac('sha1', `${secret}&`).update(text, 'utf8').digest('base64');
}

function serializeForm(form) {
  return Object.entries(form)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&');
}

function encrypt(plaintext, keyHex, ivHex) {
  const key = Buffer.from(keyHex, 'hex');
  const cipher = createCipheriv(`aes-${key.length * 8}-cbc`, key, Buffer.from(ivHex, 'hex'));
  return Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]).toString('base64');
}

function decrypt(ciphertext, keyHex, ivHex) {
  const key = Buffer.from(keyHex, 'hex');
  const decipher = createDecipheriv(`aes-${key.length * 8}-cbc`, key, Buffer.from(ivHex, 'hex'));
  return Buffer.concat([decipher.update(Buffer.from(ciphertext, 'base64')), decipher.final()]).toString('utf8');
}

function signedForm(fields, secret, nonce = randomUUID()) {
  const form = { ...fields };
  delete form.Signature;
  form.SignatureNonce = nonce;
  form.Signature = signature(form, secret);
  return form;
}

module.exports = { percentEncode, canonicalQuery, signature, serializeForm, encrypt, decrypt, signedForm };
