"""Real crypto/SQLite/ASGI with synthetic identities and mock provider only."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("oauth_fixture")
package.__path__ = [str(ROOT / "nonebot_plugin_unibot/services")]
sys.modules[package.__name__] = package
o = importlib.import_module("oauth_fixture.oauth")
runtime = importlib.import_module("oauth_fixture.oauth_runtime")


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="fixture", use="sig", alg="RS256")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = o.Settings("fixture-client", "fixture-secret", "https://example.test/oauth/lxns/callback", Fernet.generate_key().decode())
        self.store = o.OAuthStore(Path(self.temp.name) / "oauth", self.settings.encryption_key)
        self.now = time.time()
        self.codes = {}
        self.calls = []
        self.token_calls = 0
        self.refresh_calls = 0
        self.mode = "ok"
        self.overrides = {}
        self.allowed = True
        self.service = o.OAuthService(self.settings, self.store, transport=httpx.MockTransport(self.provider),
            clock=lambda: self.now, group_allowed=lambda group: self.allowed)

    def response(self, subject, nonce=None, **overrides):
        claims = dict(iss=o.ISSUER, sub=subject, aud=self.settings.client_id, iat=int(time.time()) - 1, exp=int(time.time()) + 600)
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(self.overrides)
        value = dict(access_token="access-" + subject + "-" + str(self.refresh_calls), refresh_token="refresh-" + subject + "-" + str(self.refresh_calls),
                     token_type="Bearer", expires_in=900, scope="read_player openid",
                     id_token=jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "fixture"}))
        value.update(overrides)
        return value

    async def provider(self, request):
        self.calls.append(request.url.path)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json=dict(issuer=o.ISSUER, authorization_endpoint=o.AUTHORIZE, token_endpoint=o.TOKEN,
                jwks_uri=o.JWKS, id_token_signing_alg_values_supported=["RS256"], code_challenge_methods_supported=["S256"]))
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [self.jwk]})
        self.assertEqual(str(request.url), o.TOKEN)
        self.token_calls += 1
        args = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.assertEqual(args["client_secret"], "fixture-secret")
        if args["grant_type"] == "authorization_code":
            subject, nonce, challenge = self.codes[args["code"]]
            actual = o.base64.urlsafe_b64encode(o.hashlib.sha256(args["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(actual, challenge)
            self.assertEqual(args["redirect_uri"], self.settings.redirect_uri)
            return httpx.Response(200, json=self.response(subject, nonce))
        self.refresh_calls += 1
        await asyncio.sleep(0.015)
        if self.mode == "network":
            raise httpx.ReadTimeout("secret response intentionally not propagated", request=request)
        if self.mode == "invalid_grant":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PRIVATE-DETAIL"})
        if self.mode == "rate":
            return httpx.Response(429, json={"error": "rate_limit"})
        if self.mode == "pause":
            self.started.set()
            await self.release.wait()
        subject = args["refresh_token"].split("-")[1]
        return httpx.Response(200, json=self.response(subject))

    async def begin(self, qq="7", bot="1", group=None):
        url = await self.service.begin(bot, qq, origin_group=group)
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.assertEqual(set(query["scope"].split()), {"read_player", "openid"})
        self.assertNotIn("client_secret", query)
        self.assertNotIn("code_verifier", query)
        self.assertEqual(query["code_challenge_method"], "S256")
        code = "upstream-code-" + qq
        self.codes[code] = ("subject" + qq, query["nonce"], query["code_challenge"])
        return query["state"], code

    async def bind(self, qq="7", bot="1"):
        state, code = await self.begin(qq, bot)
        proof = await self.service.callback(state, code)
        await self.service.confirm(bot, qq, proof)
        return state, code, proof

    async def test_pkce_binding_confirmation_no_legacy_writes_and_encrypted_disk(self):
        state, code = await self.begin()
        proof = await self.service.callback(state, code)
        self.assertEqual(await self.service.status("1", "7"), "awaiting_confirmation")
        with self.assertRaisesRegex(o.OAuthError, "NOT_AUTHORIZED"):
            await self.service.access_token("1", "7")
        for bot, qq in [("1", "8"), ("2", "7")]:
            with self.assertRaisesRegex(o.OAuthError, "CONFIRM_INVALID"):
                await self.service.confirm(bot, qq, proof)
        await self.service.confirm("1", "7", proof)
        self.assertTrue((await self.service.access_token("1", "7")).startswith("access-subject7"))
        with self.assertRaisesRegex(o.OAuthError, "CONFIRM_INVALID"):
            await self.service.confirm("1", "7", proof)
        data = self.store.path.read_bytes()
        for secret in [b"access-subject7", b"refresh-subject7", b"subject7", code.encode(), state.encode(), proof.encode(), b"fixture-secret"]:
            self.assertNotIn(secret, data)
        self.assertEqual(set(Path(self.temp.name).iterdir()), {self.store.directory})
        if os.name != "nt":
            self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    async def test_unknown_expired_replayed_and_superseded_states_no_exchange(self):
        state, code = await self.begin()
        replacement, new_code = await self.begin()
        for invalid in [state, "x" * 43]:
            with self.assertRaisesRegex(o.OAuthError, "STATE_INVALID"):
                await self.service.callback(invalid, code)
        self.now += 601
        with self.assertRaisesRegex(o.OAuthError, "STATE_INVALID"):
            await self.service.callback(replacement, new_code)
        self.assertEqual(self.token_calls, 0)
        self.now -= 601
        state, code, _ = await self.bind()
        with self.assertRaisesRegex(o.OAuthError, "STATE_INVALID"):
            await self.service.callback(state, code)

    async def test_denial_is_consumed_without_network_and_never_echoed(self):
        state, code = await self.begin()
        with self.assertRaisesRegex(o.OAuthError, "^AUTHORIZATION_DENIED$"):
            await self.service.callback(state, error="PRIVATE-DETAIL")
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(o.OAuthError, "STATE_INVALID"):
            await self.service.callback(state, code)

    async def test_many_authorizations_and_callback_replay_concurrently(self):
        pending = await asyncio.gather(*(self.begin(str(q)) for q in range(10, 20)))
        proofs = await asyncio.gather(*(self.service.callback(state, code) for state, code in pending))
        await asyncio.gather(*(self.service.confirm("1", str(q), proof) for q, proof in zip(range(10, 20), proofs)))
        for q in range(10, 20):
            self.assertEqual(await self.service.access_token("1", str(q)), f"access-subject{q}-0")
        state, code = await self.begin("21")
        outputs = await asyncio.gather(self.service.callback(state, code), self.service.callback(state, code), return_exceptions=True)
        self.assertEqual(sum(isinstance(v, o.OAuthError) for v in outputs), 1)

    async def test_oidc_bad_claims_are_never_bound(self):
        for overrides in [dict(nonce="wrong"), dict(iss="https://evil.test"), dict(aud="other"), dict(exp=int(time.time()) - 60),
                          dict(iat=int(time.time()) + 500), dict(azp="other"), dict(aud=["fixture-client", "other"]), dict(sub="")]:
            with self.subTest(overrides=overrides):
                self.overrides = overrides
                state, code = await self.begin()
                with self.assertRaisesRegex(o.OAuthError, "OIDC_INVALID"):
                    await self.service.callback(state, code)
                self.assertEqual(await self.service.status("1", "7"), "reauthorize")
        self.overrides = {}

    async def test_bad_signature_wrong_algorithm_and_unknown_kid_rejected(self):
        state, code = await self.begin()
        subject, nonce, _ = self.codes[code]
        value = self.response(subject, nonce)
        claims = dict(iss=o.ISSUER, sub=subject, aud=self.settings.client_id, iat=int(time.time()) - 1, exp=int(time.time()) + 500, nonce=nonce)
        values = [jwt.encode(claims, "a" * 64, algorithm="HS256", headers={"kid": "fixture"}),
                  jwt.encode(claims, rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256", headers={"kid": "fixture"}),
                  jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "unknown"})]
        for bad in values:
            with self.assertRaisesRegex(o.OAuthError, "OIDC_INVALID"):
                await self.service._credential(dict(value, id_token=bad), nonce=nonce)

    async def test_scope_token_and_discovery_validation(self):
        state, code = await self.begin()
        subject, nonce, _ = self.codes[code]
        good = self.response(subject, nonce)
        for change in [dict(scope="read_player"), dict(scope="read_player openid write_player"), dict(token_type="Basic"),
                       dict(expires_in=True), dict(refresh_token=""), dict(id_token=None)]:
            with self.subTest(change=change), self.assertRaises(o.OAuthError):
                await self.service._credential(dict(good, **change), nonce=nonce)
        async def evil(request):
            return httpx.Response(200, json=dict(issuer=o.ISSUER, authorization_endpoint=o.AUTHORIZE, token_endpoint="https://evil.test", jwks_uri=o.JWKS))
        self.service.transport = httpx.MockTransport(evil)
        with self.assertRaisesRegex(o.OAuthError, "OIDC_INVALID"):
            await self.service._identity(good["id_token"], nonce)

    async def test_refresh_singleflight_and_rotation_for_many_users(self):
        await self.bind()
        await self.bind("8")
        self.now += 850
        values = await asyncio.gather(*(self.service.access_token("1", "7") for _ in range(15)), self.service.access_token("1", "8"))
        self.assertEqual(len(set(values[:-1])), 1)
        self.assertIn("subject8", values[-1])
        self.assertEqual(self.refresh_calls, 2)
        self.assertEqual(self.service._locks, {})
        self.assertEqual(await self.service.access_token("1", "7"), values[0])

    async def test_two_service_instances_do_not_reuse_refresh_token(self):
        await self.bind()
        self.now += 850
        other = o.OAuthService(self.settings, o.OAuthStore(self.store.directory, self.settings.encryption_key),
                              transport=self.service.transport, clock=lambda: self.now)
        results = await asyncio.gather(self.service.access_token("1", "7"), other.access_token("1", "7"), return_exceptions=True)
        self.assertEqual(self.refresh_calls, 1)
        self.assertEqual(sum(isinstance(v, o.OAuthError) for v in results), 1)

    async def test_stale_snapshot_cannot_refresh_after_other_instance_finishes(self):
        await self.bind()
        stale = self.store.get("1:7")
        self.now += 850
        fresh = await self.service.access_token("1", "7")
        with self.assertRaisesRegex(o.OAuthError, "BUSY"):
            self.store.reserve_refresh("1:7", stale["generation"], stale["payload"], self.now, "stale-reservation")
        self.assertEqual(await self.service.access_token("1", "7"), fresh)
        self.assertEqual(self.refresh_calls, 1)

    async def test_old_refresh_cleanup_cannot_overwrite_new_reservation(self):
        await self.bind()
        original = self.store.get("1:7")
        self.store.reserve_refresh("1:7", original["generation"], original["payload"], self.now, "operation-A")
        value = self.store.unpack(original["payload"])
        value["access_token"] = "rotated-access"
        self.assertTrue(self.store.save_refresh("1:7", "operation-A", "active", value, self.now))
        current = self.store.get("1:7")
        self.store.reserve_refresh("1:7", current["generation"], current["payload"], self.now, "operation-B")
        # Cancellation after A's commit must not invalidate B or restore A's old token.
        self.assertFalse(self.store.save_refresh("1:7", "operation-A", "refresh_unknown", {}, self.now))
        self.assertEqual(self.store.get("1:7")["generation"], "operation-B")
        self.assertEqual(self.store.unpack(self.store.get("1:7")["payload"])["access_token"], "rotated-access")

    async def test_refresh_revoke_timeout_rate_limit_no_blind_retry(self):
        for mode in ["invalid_grant", "network", "rate"]:
            await self.service.unlink("1", "7")
            await self.bind()
            self.now += 850
            self.mode = mode
            before = self.refresh_calls
            with self.assertRaises(o.OAuthError) as result:
                await self.service.access_token("1", "7")
            self.assertNotIn("PRIVATE-DETAIL", str(result.exception))
            self.assertEqual(await self.service.status("1", "7"), "reauthorize" if mode == "invalid_grant" else "refresh_unknown")
            with self.assertRaisesRegex(o.OAuthError, "NOT_AUTHORIZED"):
                await self.service.access_token("1", "7")
            self.assertEqual(self.refresh_calls, before + 1)
            self.mode = "ok"

    async def test_refresh_subject_change_and_401_stale_response(self):
        await self.bind()
        old = await self.service.access_token("1", "7")
        self.now += 850
        fresh = await self.service.access_token("1", "7")
        await self.service.invalidate_access("1", "7", old)
        self.assertEqual(await self.service.status("1", "7"), "active")
        await self.service.invalidate_access("1", "7", fresh)
        self.assertEqual(await self.service.status("1", "7"), "reauthorize")
        await self.bind()
        self.now += 850
        self.overrides = {"sub": "attacker"}
        with self.assertRaisesRegex(o.OAuthError, "OIDC_INVALID"):
            await self.service.access_token("1", "7")
        self.assertNotEqual(await self.service.status("1", "7"), "active")

    async def test_unlink_during_refresh_does_not_resurrect_credentials(self):
        await self.bind()
        self.now += 850
        self.mode = "pause"
        self.started, self.release = asyncio.Event(), asyncio.Event()
        work = asyncio.create_task(self.service.access_token("1", "7"))
        await self.started.wait()
        self.assertEqual(await self.service.unlink("1", "7"), "remote_revocation_unavailable")
        self.release.set()
        with self.assertRaisesRegex(o.OAuthError, "NOT_AUTHORIZED"):
            await work
        self.assertEqual(await self.service.status("1", "7"), "unbound")

    async def test_unlink_pending_cancels_callback_and_qq_confirmation(self):
        state, code = await self.begin()
        await self.service.unlink("1", "7")
        with self.assertRaisesRegex(o.OAuthError, "STATE_INVALID"):
            await self.service.callback(state, code)
        state, code = await self.begin()
        proof = await self.service.callback(state, code)
        await self.service.unlink("1", "7")
        with self.assertRaisesRegex(o.OAuthError, "CONFIRM_INVALID"):
            await self.service.confirm("1", "7", proof)

    async def test_confirmation_expiry_existing_binding_and_unique_external_identity(self):
        state, code = await self.begin()
        proof = await self.service.callback(state, code)
        with self.assertRaisesRegex(o.OAuthError, "CONFIRM_PENDING"):
            await self.begin()
        self.now += 301
        with self.assertRaisesRegex(o.OAuthError, "CONFIRM_INVALID"):
            await self.service.confirm("1", "7", proof)
        await self.bind()
        with self.assertRaisesRegex(o.OAuthError, "ALREADY_BOUND"):
            await self.begin()
        state, code = await self.begin("8")
        _, nonce, challenge = self.codes[code]
        self.codes[code] = ("subject7", nonce, challenge)
        with self.assertRaisesRegex(o.OAuthError, "ACCOUNT_IN_USE"):
            await self.service.callback(state, code)
        self.assertEqual(await self.service.status("1", "7"), "active")

    async def test_expired_unconfirmed_subject_does_not_block_other_qq(self):
        state, code = await self.begin()
        proof = await self.service.callback(state, code)
        self.now += 301
        state, code = await self.begin("8")
        _, nonce, challenge = self.codes[code]
        self.codes[code] = ("subject7", nonce, challenge)
        new_proof = await self.service.callback(state, code)
        await self.service.confirm("1", "8", new_proof)
        with self.assertRaisesRegex(o.OAuthError, "CONFIRM_INVALID"):
            await self.service.confirm("1", "7", proof)

    async def test_group_policy_rechecked_before_exchange_and_confirmation(self):
        state, code = await self.begin(group=123)
        self.allowed = False
        with self.assertRaisesRegex(o.OAuthError, "FEATURE_DISABLED"):
            await self.service.callback(state, code)
        self.assertEqual(self.token_calls, 0)
        with self.assertRaisesRegex(o.OAuthError, "FEATURE_DISABLED"):
            await self.begin(group=123)
        self.allowed = True
        state, code = await self.begin(group=123)
        proof = await self.service.callback(state, code)
        self.allowed = False
        with self.assertRaisesRegex(o.OAuthError, "FEATURE_DISABLED"):
            await self.service.confirm("1", "7", proof)

    async def test_wrong_key_fails_closed_and_config_changes_cannot_refresh(self):
        with self.assertRaisesRegex(o.OAuthError, "STORAGE_KEY_INVALID"):
            o.OAuthStore(self.store.directory, Fernet.generate_key())
        await self.bind()
        other_settings = o.Settings("other-client", "other-secret", self.settings.redirect_uri, self.settings.encryption_key)
        other = o.OAuthService(other_settings, self.store, transport=self.service.transport)
        with self.assertRaisesRegex(o.OAuthError, "NOT_CONFIGURED"):
            await other.access_token("1", "7")

    async def test_asgi_callback_sanitizes_query_errors_headers_and_duplicates(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.add_middleware(runtime.ScrubCallbackQuery)
        app.add_route(o.CALLBACK_PATH, runtime.callback_endpoint(self.service), methods=["GET"])
        observed = []
        async def wrapped(scope, receive, send):
            await app(scope, receive, send)
            observed.append(scope.get("query_string"))
            self.assertNotIn("_unibot_oauth_query", scope)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="https://example.test") as client:
            state, code = await self.begin()
            result = await client.get(o.CALLBACK_PATH, params={"state": state, "code": code})
            self.assertEqual(result.status_code, 200)
            self.assertIn("/落雪确认", result.text)
            self.assertEqual(result.headers["cache-control"], "no-store")
            for secret in [state, code, "subject7", "access-subject7", "fixture-secret"]:
                self.assertNotIn(secret, result.text)
            result = await client.get(o.CALLBACK_PATH, params={"state": "x" * 43, "error": "PRIVATE-DETAIL"})
            self.assertEqual(result.status_code, 400)
            self.assertNotIn("PRIVATE-DETAIL", result.text)
            state, code = await self.begin("8")
            result = await client.get(o.CALLBACK_PATH, params=[("state", state), ("code", code), ("state", state)])
            self.assertEqual(result.status_code, 400)
        self.assertEqual(observed, [b"", b"", b""])


if __name__ == "__main__":
    unittest.main()
