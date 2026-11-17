"""LXNS OAuth/OIDC. Internal token API; never expose it as an LLM capability."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from .job_store import async_call
from .oauth_store import OAuthError, OAuthStore

ISSUER = "https://maimai.lxns.net"
AUTHORIZE = ISSUER + "/oauth/authorize"
TOKEN = ISSUER + "/api/v0/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
CALLBACK_PATH = "/oauth/lxns/callback"
SCOPES = frozenset({"read_player", "openid"})


class AuthorizationLink(str):
    def __new__(cls, text, generation):
        result = super().__new__(cls, text)
        result.generation = generation
        return result


@dataclass(frozen=True, repr=False)
class Settings:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    encryption_key: str = field(repr=False)

    def __post_init__(self):
        uri = urlsplit(self.redirect_uri)
        if not self.client_id or not self.client_secret or uri.scheme != "https" or not uri.hostname or uri.username or uri.password or uri.path != CALLBACK_PATH or uri.query or uri.fragment:
            raise OAuthError("NOT_CONFIGURED")

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps([self.client_id, self.redirect_uri, ISSUER]).encode()).hexdigest()


def owner_key(bot, qq):
    values = [str(bot), str(qq)]
    if any(not v.isascii() or not v.isdigit() or int(v) <= 0 for v in values):
        raise OAuthError("IDENTITY_INVALID")
    return ":".join(str(int(v)) for v in values)


class OAuthService:
    def __init__(self, settings, store, *, transport=None, clock=time.time, group_allowed=lambda group: False):
        self.settings = settings
        self.store: OAuthStore = store
        self.transport = transport
        self.clock = clock
        self.group_allowed = group_allowed
        self._locks = {}  # Only active operations; removed when the last waiter exits.
        self._invalidators = []
        self._metadata = None
        self._keys = None
        self._keys_at = 0

    async def _http(self, method, url, data=None):
        # No retries for rotating credentials; no redirects or environment proxies.
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async with client.stream(method, url, data=data) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 262144:
                            raise OAuthError("UPSTREAM_INVALID")
                    try:
                        body = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise OAuthError("UPSTREAM_INVALID") from None
                    if not isinstance(body, dict):
                        raise OAuthError("UPSTREAM_INVALID")
                    if response.status_code >= 400:
                        error = body.get("error")
                        if error == "invalid_grant":
                            raise OAuthError("GRANT_INVALID")
                        if response.status_code == 429:
                            raise OAuthError("RATE_LIMITED")
                        raise OAuthError("UPSTREAM_FAILED")
                    if response.status_code != 200 or "error" in body:
                        raise OAuthError("UPSTREAM_INVALID")
                    return body
        except (httpx.HTTPError, OSError):
            raise OAuthError("NETWORK_FAILED") from None

    async def _discovery(self):
        if self._metadata is None:
            value = await self._http("GET", ISSUER + "/.well-known/openid-configuration")
            for name, expected in {"issuer": ISSUER, "authorization_endpoint": AUTHORIZE, "token_endpoint": TOKEN, "jwks_uri": JWKS}.items():
                if value.get(name) != expected:
                    raise OAuthError("OIDC_INVALID")
            if "RS256" not in value.get("id_token_signing_alg_values_supported", []) or "S256" not in value.get("code_challenge_methods_supported", []):
                raise OAuthError("OIDC_INVALID")
            self._metadata = value
        return self._metadata

    async def _identity(self, token, nonce=None):
        await self._discovery()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise OAuthError("OIDC_INVALID")
            for attempt in range(2):
                if attempt or self._keys is None or self._keys_at + 300 < self.clock():
                    self._keys = (await self._http("GET", JWKS)).get("keys", [])
                    self._keys_at = self.clock()
                keys = [k for k in self._keys if isinstance(k, dict) and k.get("kid") == header["kid"]
                        and k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256"]
                if len(keys) == 1:
                    break
            if len(keys) != 1:
                raise OAuthError("OIDC_INVALID")
            claims = jwt.decode(token, jwt.algorithms.RSAAlgorithm.from_jwk(keys[0]), algorithms=["RS256"],
                                audience=self.settings.client_id, issuer=ISSUER,
                                options={"require": ["iss", "sub", "aud", "exp", "iat"]})
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
                raise OAuthError("OIDC_INVALID")
            aud = claims["aud"]
            if (isinstance(aud, list) and len(aud) > 1 or "azp" in claims) and claims.get("azp") != self.settings.client_id:
                raise OAuthError("OIDC_INVALID")
            if nonce is not None and (not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce)):
                raise OAuthError("OIDC_INVALID")
            return claims["sub"]
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise OAuthError("OIDC_INVALID") from None

    async def _credential(self, value, *, nonce=None, subject=None):
        try:
            if value["token_type"].lower() != "bearer" or set(value["scope"].split()) != SCOPES:
                raise OAuthError("TOKEN_INVALID")
            ttl = value["expires_in"]
            if type(ttl) is not int or not 0 < ttl <= 86400:
                raise OAuthError("TOKEN_INVALID")
            for name in ("access_token", "refresh_token", "id_token"):
                if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 16384:
                    raise OAuthError("TOKEN_INVALID")
            identity = await self._identity(value["id_token"], nonce)
            if subject is not None and not hmac.compare_digest(subject, identity):
                raise OAuthError("OIDC_INVALID")
            # Do not retain the ID Token, claims, profile, email or upstream body.
            return dict(access_token=value["access_token"], refresh_token=value["refresh_token"],
                        expires_at=self.clock() + ttl, subject=identity, issuer=ISSUER,
                        config=self.settings.fingerprint)
        except (KeyError, AttributeError, TypeError):
            raise OAuthError("TOKEN_INVALID") from None

    def _client(self):
        return dict(client_id=self.settings.client_id, client_secret=self.settings.client_secret)

    def _policy(self, group):
        if group is not None and not self.group_allowed(group):
            raise OAuthError("FEATURE_DISABLED")

    async def begin(self, bot, qq, *, origin_group=None):
        self._policy(origin_group)
        owner = owner_key(bot, qq)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        generation = secrets.token_hex(16)
        await async_call(self.store.begin, owner, generation, hashlib.sha256(state.encode()).hexdigest(),
                         dict(nonce=nonce, verifier=verifier, config=self.settings.fingerprint, origin_group=origin_group), self.clock())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return AuthorizationLink(AUTHORIZE + "?" + urlencode(dict(response_type="code", client_id=self.settings.client_id,
            redirect_uri=self.settings.redirect_uri, scope="read_player openid", state=state,
            nonce=nonce, code_challenge=challenge, code_challenge_method="S256")), generation)

    async def callback(self, state, code=None, error=None):
        if not isinstance(state, str) or not 32 <= len(state) <= 128:
            raise OAuthError("STATE_INVALID")
        request, pending = await async_call(self.store.claim, hashlib.sha256(state.encode()).hexdigest(), self.clock())
        try:
            self._policy(pending.get("origin_group"))
            if pending["config"] != self.settings.fingerprint:
                raise OAuthError("NOT_CONFIGURED")
            if error is not None:
                raise OAuthError("AUTHORIZATION_DENIED")
            if not isinstance(code, str) or not 1 <= len(code) <= 2048:
                raise OAuthError("CALLBACK_INVALID")
            value = await self._http("POST", TOKEN, dict(self._client(), grant_type="authorization_code", code=code,
                                    redirect_uri=self.settings.redirect_uri, code_verifier=pending["verifier"]))
            credential = await self._credential(value, nonce=pending["nonce"])
            proof = secrets.token_urlsafe(16)  # Local QQ confirmation, NOT an OAuth authorization code.
            credential.update(confirm_hash=hashlib.sha256(proof.encode()).hexdigest(), confirm_expires=self.clock() + 300)
            credential["origin_group"] = pending.get("origin_group")
            bot = request["owner"].split(":")[0]
            self._policy(pending.get("origin_group"))
            await async_call(self.store.finish, request, credential,
                self.store.subject_key(bot, ISSUER, credential["subject"]), self.clock())
            return proof
        except BaseException:
            await async_call(self.store.fail_request, request, self.clock())
            raise

    async def confirm(self, bot, qq, proof):
        if not isinstance(proof, str) or not 1 <= len(proof) <= 64:
            raise OAuthError("CONFIRM_INVALID")
        owner = owner_key(bot, qq)
        row = await async_call(self.store.get, owner)
        if row and row["status"] == "awaiting_confirmation":
            self._policy(self.store.unpack(row["payload"]).get("origin_group"))
        return await async_call(self.store.confirm, owner, proof, self.clock())

    async def status(self, bot, qq):
        row = await async_call(self.store.get, owner_key(bot, qq))
        if not row:
            return "unbound"
        if row["status"] == "awaiting_confirmation" and self.store.unpack(row["payload"])["confirm_expires"] <= self.clock():
            return "reauthorize"
        if row["status"] == "refreshing" and row["updated"] + 120 <= self.clock():
            return "refresh_unknown"
        if row["status"] == "authorizing" and row["updated"] + 600 <= self.clock():
            return "reauthorize"
        return row["status"]

    async def unlink(self, bot, qq):
        # Provider currently publishes no revocation endpoint. Never invent one.
        # Deletion also cancels pending callbacks and defeats late refresh writes.
        await async_call(self.store.unlink, owner_key(bot, qq))
        for invalidate in self._invalidators:
            invalidate(str(bot), str(qq))
        return "remote_revocation_unavailable"

    async def access_token(self, bot, qq):
        """Phase 3B internal-only entry; caller must enforce its current policy."""
        owner = owner_key(bot, qq)
        lock, users = self._locks.get(owner, (asyncio.Lock(), 0))
        self._locks[owner] = (lock, users + 1)
        try:
            async with lock:
                row = await async_call(self.store.get, owner)
                if row and row["status"] == "refreshing" and row["updated"] + 120 > self.clock():
                    raise OAuthError("BUSY")
                if not row or row["status"] != "active":
                    raise OAuthError("NOT_AUTHORIZED")
                value = self.store.unpack(row["payload"])
                if value["config"] != self.settings.fingerprint:
                    raise OAuthError("NOT_CONFIGURED")
                if value["expires_at"] > self.clock() + 60:
                    return value["access_token"]
                reservation = secrets.token_hex(16)
                await async_call(self.store.reserve_refresh, owner, row["generation"], row["payload"], self.clock(), reservation)
                try:
                    reply = await self._http("POST", TOKEN, dict(self._client(), grant_type="refresh_token", refresh_token=value["refresh_token"]))
                    updated = await self._credential(reply, subject=value["subject"])
                    updated["binding_id"] = value.get("binding_id") or secrets.token_hex(16)
                    saved = await async_call(self.store.save_refresh, owner, reservation, "active", updated, self.clock())
                    if not saved:
                        raise OAuthError("NOT_AUTHORIZED")
                    return updated["access_token"]
                except BaseException as error:
                    status = "reauthorize" if isinstance(error, OAuthError) and str(error) == "GRANT_INVALID" else "refresh_unknown"
                    # A lost response may already have rotated the refresh token.
                    # Retain encrypted evidence, disable use, never blindly retry.
                    await async_call(self.store.save_refresh, owner, reservation, status,
                                     {} if status == "reauthorize" else value, self.clock())
                    raise
        finally:
            current_lock, users = self._locks[owner]
            if users == 1:
                del self._locks[owner]
            else:
                self._locks[owner] = (current_lock, users - 1)

    async def invalidate_access(self, bot, qq, rejected_token):
        """3B uses this after a definite 401; a stale response cannot revoke a new token."""
        changed = await async_call(self.store.invalidate, owner_key(bot, qq), rejected_token, self.clock())
        if changed:
            for invalidate in self._invalidators:
                invalidate(str(bot), str(qq))
        return changed

    async def credential_lease(self, bot, qq):
        await self.access_token(bot, qq)
        return await async_call(self.store.credential_lease, owner_key(bot, qq), self.settings.fingerprint)

    async def binding_matches(self, bot, qq, binding):
        return await async_call(self.store.binding_matches, owner_key(bot, qq), binding, self.settings.fingerprint)

    async def current_binding(self, bot, qq):
        return await async_call(self.store.current_binding, owner_key(bot, qq), self.settings.fingerprint)
