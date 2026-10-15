"""Offline protocol regressions with public vectors and generated fixtures only.

No test contacts a model or a captcha service. Cryptographic checks use the
NIST SP 800-38A CBC examples and independent PyCryptodome decryption.
"""

import base64
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import unpad
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import geetest_load as load
import geetest_verify as verify


class CryptoTests(unittest.TestCase):
    def test_nist_cbc_vectors_for_all_supported_key_sizes(self):
        # NIST SP 800-38A, appendix F.2.1/F.2.3/F.2.5. These examples do
        # not have padding; this API appends a fifth PKCS#7 block.
        plaintext = bytes.fromhex(
            "6bc1bee22e409f96e93d7e117393172a"
            "ae2d8a571e03ac9c9eb76fac45af8e51"
            "30c81c46a35ce411e5fbc1191a0a52ef"
            "f69f2445df4f9b17ad2b417be66c3710")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        vectors = [
            ("2b7e151628aed2a6abf7158809cf4f3c",
             "7649abac8119b246cee98e9b12e9197d"
             "5086cb9b507219ee95db113a917678b2"
             "73bed6b8e3c1743b7116e69e22229516"
             "3ff1caa1681fac09120eca307586e1a7"),
            ("8e73b0f7da0e6452c810f32b809079e562f8ead2522c6b7b",
             "4f021db243bc633d7178183a9fa071e8"
             "b4d9ada9ad7dedf4e5e738763f69145a"
             "571b242012fb7ae07fa9baac3df102e0"
             "08b0e27988598881d920a9e64f5615cd"),
            ("603deb1015ca71be2b73aef0857d7781"
             "1f352c073b6108d72d9810a30914dff4",
             "f58c4c04d6e5f1ba779eabfb5f7bfbd6"
             "9cfc4e967edb808d679f777bc6702c7d"
             "39f23369a9d9bacfa530e26304231461"
             "b2eb05e2c39be9fcda6c19078c6a9d1b"),
        ]
        for engine in (verify._aes_cbc_encrypt_pure, verify._aes_cbc_encrypt):
            for key_hex, expected_hex in vectors:
                with self.subTest(engine=engine.__name__, key_bits=len(key_hex) * 4):
                    actual = engine(plaintext, bytes.fromhex(key_hex), iv)
                    self.assertEqual(actual[:64], bytes.fromhex(expected_hex))
                    self.assertEqual(len(actual), 80)

    def test_pkcs7_and_unicode_bytes_with_independent_decryption(self):
        iv = bytes(range(16))
        for size in (0, 1, 15, 16, 17, 31, 32, 33, 63, 64, 65):
            plaintext = bytes(index % 256 for index in range(size))
            for key_size in (16, 24, 32):
                key = bytes(range(key_size))
                with self.subTest(size=size, key_size=key_size):
                    ciphertext = verify._aes_cbc_encrypt_pure(plaintext, key, iv)
                    padded = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
                    self.assertEqual(unpad(padded, 16), plaintext)
                    self.assertEqual(len(ciphertext), (size // 16 + 1) * 16)
        text = json.dumps({"example": "离线合成文本"}, ensure_ascii=False).encode("utf-8")
        ciphertext = verify._aes_cbc_encrypt(text, b"0123456789abcdef", verify.AES_IV)
        padded = AES.new(b"0123456789abcdef", AES.MODE_CBC, verify.AES_IV).decrypt(ciphertext)
        self.assertEqual(unpad(padded, 16), text)

    def test_rsa_pkcs1_v15_with_independent_decryption(self):
        # This is an ephemeral test key, never a service key or saved fixture.
        key = RSA.generate(1024)
        message = b"offline-example!"
        ciphertext_hex = verify.rsa_encrypt_pkcs1(message, key.n, key.e)
        ciphertext = int(ciphertext_hex, 16).to_bytes(key.size_in_bytes(), "big")
        self.assertEqual(PKCS1_v1_5.new(key).decrypt(ciphertext, b"invalid"), message)

    def test_rsa_message_must_leave_eleven_bytes_for_padding(self):
        with self.assertRaises(ValueError):
            verify.rsa_encrypt_pkcs1(b"x" * 118)

    def test_fixed_aes_key_retries_a_short_rsa_suffix(self):
        short, full = "ab" * 127, "cd" * 128
        key = "0123456789abcdef"
        with patch.object(verify, "rsa_encrypt_pkcs1", side_effect=[short, full]) as encrypt:
            result = verify.build_w({"synthetic": 1}, key=key)
        self.assertEqual(encrypt.call_count, 2)
        self.assertEqual([call.args[0] for call in encrypt.call_args_list], [key.encode()] * 2)
        self.assertEqual(result["key"], key)
        self.assertEqual(result["rsa_hex"], full)
        self.assertEqual(result["w"], result["cipher_hex"] + full)

    def test_rsa_retry_budget_cannot_return_a_malformed_suffix(self):
        with patch.object(verify, "rsa_encrypt_pkcs1", return_value="ab" * 127) as encrypt:
            with self.assertRaises(RuntimeError):
                verify.build_w({"synthetic": 1}, key="0123456789abcdef", retries=2)
        self.assertEqual(encrypt.call_count, 2)

    def test_generated_key_is_replaced_after_short_rsa_output(self):
        keys = ["0123456789abcdef", "fedcba9876543210"]
        with patch.object(verify, "guid", side_effect=keys), \
                patch.object(verify, "rsa_encrypt_pkcs1", side_effect=["ab" * 127, "cd" * 128]):
            result = verify.build_w({"synthetic": 1})
        self.assertEqual(result["key"], keys[1])
        self.assertEqual(len(result["rsa_hex"]), verify.RSA_HEX_LEN)

    def test_pt_zero_is_exact_local_base64_encoding_only(self):
        # This establishes local serialization, not acceptance of pt=0 online.
        payload = {"a": 1, "text": "合成"}
        result = verify.build_w(payload, "0")
        expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(result["w"], base64.urlsafe_b64encode(expected).decode("ascii"))
        self.assertIsNone(result["cipher_hex"])
        self.assertIsNone(result["rsa_hex"])


class PowTests(unittest.TestCase):
    def test_hashes_and_partial_nibble_difficulties(self):
        for name in ("md5", "sha1", "sha256"):
            width = hashlib.new(name).digest_size * 8
            for bits in range(13):
                with self.subTest(hash=name, bits=bits):
                    nonces = (format(nonce, "016x") for nonce in range(100000))
                    with patch.object(verify, "guid", side_effect=lambda: next(nonces)):
                        result = verify.build_pow("fixture-lot", bits, name,
                                                  "2000-01-01 00:00:00", "fixture-captcha")
                    expected = hashlib.new(name, result["pow_msg"].encode("utf-8")).hexdigest()
                    self.assertEqual(result["pow_sign"], expected)
                    # Integer comparison checks bits, independently of hex-nibble logic.
                    self.assertLess(int(expected, 16), 1 << (width - bits))
                    self.assertEqual(result["pow_msg"].split("|")[:7],
                                     ["1", str(bits), name, "2000-01-01 00:00:00",
                                      "fixture-captcha", "fixture-lot", ""])

    def test_invalid_difficulty_is_rejected_before_nonce_search(self):
        with patch.object(verify, "guid", side_effect=AssertionError("nonce search must not start")):
            for bits in (-1, 129, True, 1.5, "1"):
                with self.subTest(bits=bits), self.assertRaises(ValueError):
                    verify.build_pow("fixture-lot", bits, "md5", "fixture-date", "fixture-captcha")
            for name, bits in (("sha1", 161), ("sha256", 257), ("unknown", 0)):
                with self.subTest(hash=name), self.assertRaises(ValueError):
                    verify.build_pow("fixture-lot", bits, name, "fixture-date", "fixture-captcha")


class EncodingTests(unittest.TestCase):
    def test_jsonp_with_semicolon_whitespace_bom_and_plain_json(self):
        expected = {"status": "success", "data": {"result": "success"}}
        encoded = json.dumps(expected)
        for text, callback in ((encoded, ""), ("callback(" + encoded + ")", "callback"),
                               ("callback(" + encoded + ");", "callback"),
                               (" \ufeffcallback(" + encoded + "); \n", "callback"),
                               ("callback(" + encoded + ");", "")):
            with self.subTest(callback=callback, suffix=text[-4:]):
                self.assertEqual(load.unwrap_jsonp(text, callback), expected)

    def test_jsonp_must_contain_an_object(self):
        for text in ("[]", "null", '"text"', "callback([]);", "callback({invalid});"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                load.unwrap_jsonp(text, "callback")

    def test_coordinate_ties_match_javascript_math_round(self):
        payload = verify.build_payload("fixture-lot", [[1, 5], [9, 13]], (20000, 20000),
                                       1000, "fixture-pow", "fixture-sign")
        self.assertEqual(payload["userresponse"], [[1, 3], [5, 7]])

    def test_coordinate_scaling_keeps_target_order_and_axis_sizes(self):
        payload = verify.build_payload("fixture-lot", [[225, 160], [15, 10]], (300, 200),
                                       1000, "fixture-pow", "fixture-sign")
        self.assertEqual(payload["userresponse"], [[7500, 8000], [500, 500]])
        self.assertEqual(payload["passtime"], 1000)

    def test_submission_serializes_nondefault_protocol_and_parses_jsonp(self):
        response = b'fixture_callback({"status":"success","data":{"result":"success"}});'
        with patch.object(verify, "http_get", return_value=(200, "text/javascript", response)) as http:
            result = verify.submit_verify("fixture-captcha", "fixture-challenge", "fixture-lot",
                                          "fixture-payload", "fixture-process", "fixture-w",
                                          payload_protocol="2", callback="fixture_callback", cookie="")
        query = parse_qs(urlparse(http.call_args.args[0]).query)
        self.assertEqual(query["payload_protocol"], ["2"])
        self.assertEqual(query["w"], ["fixture-w"])
        self.assertEqual(result["data"]["result"], "success")

    def test_full_flow_forwards_load_payload_protocol(self):
        image = Image.new("RGB", (60, 40), "white")
        output = io.BytesIO()
        image.save(output, "PNG")
        blob = output.getvalue()
        data = {"captcha_type": "icon", "imgs": "main.png", "ques": ["target.png"],
                "lot_number": "fixture-lot", "payload": "fixture-payload",
                "process_token": "fixture-process", "payload_protocol": 2, "pt": "1",
                "pow_detail": {"bits": 0, "hashfunc": "md5", "datetime": "fixture-date"}}
        fake_load = SimpleNamespace(
            DEFAULT_HEADERS={}, DEFAULT_COOKIE="",
            DEFAULT_PARAMS={"captcha_id": "fixture-captcha", "challenge": "fixture-challenge"},
            prepare_load_params=lambda params: dict(params),
            build_load_url=lambda params: "https://example.invalid/load",
            http_get=lambda *args: (200, "application/json", b"{}"),
            unwrap_jsonp=lambda *args: {"status": "success", "data": data},
            collect_image_paths=lambda value: [("imgs", "main.png"), ("ques[0]", "target.png")])

        def split_images(value, headers, timeout, image_callback=None):
            image_callback("imgs", blob)
            image_callback("ques[0]", blob)
            return "imgs", blob, [("ques[0]", blob)], {}, "icon"

        fake_solver = SimpleNamespace(
            MODEL="fixture-model", split_images=split_images,
            load_rgb=lambda value: image, prepare_main=lambda value, scale: image,
            prep_icon=lambda value: image, data_url=lambda value: "fixture-data-url",
            locate_all=lambda *args, **kwargs: ([{"index": 1, "found": True,
                "center": [30, 20], "box": [25, 15, 35, 25], "verified": True}], [], [], {}))
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(record_dir=directory, timeout=10, cookie="", lang="zho")
            with patch.object(verify, "gl", fake_load), \
                    patch.dict(sys.modules, {"geetest_solve": fake_solver}), \
                    patch.object(verify, "build_w", return_value={"w": "fixture-w"}), \
                    patch.object(verify, "submit_verify", return_value={"status": "success",
                        "data": {"result": "success"}}) as submit, redirect_stderr(io.StringIO()):
                code, _, record = verify.attempt_once(args)
        self.assertEqual(code, 0)
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(submit.call_args.kwargs["payload_protocol"], "2")


if __name__ == "__main__":
    unittest.main()
