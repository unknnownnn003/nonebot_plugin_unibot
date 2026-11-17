"""Encrypted OAuth state only. This store never reads legacy binds or scores."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import secrets

from cryptography.fernet import Fernet


class OAuthError(Exception):
    """Only fixed public codes may cross the service boundary."""


def windows_private(path):
    """Replace the DACL, including pre-existing explicit grants, not just chmod."""
    import ctypes
    from ctypes import wintypes
    sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                         check=True, capture_output=True, text=True, creationflags=0x08000000).stdout.strip().split(',')[-1].strip('"')
    if not sid.startswith("S-1-"):
        raise OAuthError("STORAGE_UNSAFE")
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    apply = security.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise OAuthError("STORAGE_UNSAFE")
    try:
        if not apply(str(path), 0x80000004, descriptor):  # protected DACL
            raise OAuthError("STORAGE_UNSAFE")
    finally:
        kernel.LocalFree(descriptor)


def private_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OAuthError("STORAGE_UNSAFE")
    if os.name == "nt":
        windows_private(path)
    else:
        if info.st_uid != os.getuid():
            raise OAuthError("STORAGE_UNSAFE")
        path.chmod(0o700)


class OAuthStore:
    def __init__(self, directory, key):
        self.directory = Path(directory)
        self.path = self.directory / "credentials.sqlite3"
        self.cipher = Fernet(key)
        self.index_key = hashlib.sha256(key.encode() if isinstance(key, str) else key).digest()
        private_directory(self.directory)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OAuthError("STORAGE_UNSAFE")
        if os.name == "nt":
            windows_private(self.path)
        else:
            if info.st_uid != os.getuid():
                raise OAuthError("STORAGE_UNSAFE")
            self.path.chmod(0o600)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('key_check',?)", (self.pack({"v": 1}),))
            if self.unpack(db.execute("SELECT value FROM meta WHERE key='key_check'").fetchone()[0]) != {"v": 1}:
                raise OAuthError("STORAGE_KEY_INVALID")
            db.execute("""CREATE TABLE IF NOT EXISTS accounts (
                owner TEXT PRIMARY KEY, generation TEXT NOT NULL, status TEXT NOT NULL,
                updated REAL NOT NULL, subject_key TEXT UNIQUE, payload BLOB NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                state_hash TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE, generation TEXT NOT NULL,
                expires REAL NOT NULL, status TEXT NOT NULL, payload BLOB NOT NULL)""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def pack(self, value):
        return self.cipher.encrypt(json.dumps(value, separators=(",", ":")).encode())

    def unpack(self, value):
        try:
            return json.loads(self.cipher.decrypt(value))
        except Exception:
            raise OAuthError("STORAGE_KEY_INVALID") from None

    def subject_key(self, bot, issuer, subject):
        return hmac.new(self.index_key, json.dumps([bot, issuer, subject]).encode(), hashlib.sha256).hexdigest()

    def get(self, owner):
        with self.connection() as db:
            row = db.execute("SELECT * FROM accounts WHERE owner=?", (owner,)).fetchone()
            return dict(row) if row else None

    def _expire_confirmations(self, db, now):
        for row in db.execute("SELECT owner,payload FROM accounts WHERE status='awaiting_confirmation'").fetchall():
            if self.unpack(row["payload"])["confirm_expires"] <= now:
                db.execute("UPDATE accounts SET status='reauthorize',subject_key=NULL,payload=?,updated=? WHERE owner=?",
                           (self.pack({}), now, row["owner"]))

    def begin(self, owner, generation, state_hash, payload, now):
        with self.connection() as db:
            self._expire_confirmations(db, now)
            row = db.execute("SELECT * FROM accounts WHERE owner=?", (owner,)).fetchone()
            if row and row["status"] in {"active", "awaiting_confirmation"}:
                if row["status"] == "active" or self.unpack(row["payload"])["confirm_expires"] > now:
                    raise OAuthError("ALREADY_BOUND" if row["status"] == "active" else "CONFIRM_PENDING")
            if row and row["status"] == "refreshing" and row["updated"] + 120 > now:
                raise OAuthError("BUSY")
            db.execute("DELETE FROM requests WHERE expires<=? OR owner=?", (now, owner))
            db.execute("DELETE FROM meta WHERE key=?", ("link_notice:" + owner,))
            db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?, ?,NULL,?)",
                       (owner, generation, "authorizing", now, self.pack({})))
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)",
                       (state_hash, owner, generation, now + 600, "pending", self.pack(payload)))

    def claim(self, state_hash, now):
        with self.connection() as db:
            row = db.execute("SELECT * FROM requests WHERE state_hash=?", (state_hash,)).fetchone()
            if not row or row["status"] != "pending" or row["expires"] <= now:
                raise OAuthError("STATE_INVALID")
            db.execute("UPDATE requests SET status='exchanging' WHERE state_hash=?", (state_hash,))
            return dict(row), self.unpack(row["payload"])

    def finish(self, request, payload, subject_key, now):
        with self.connection() as db:
            self._expire_confirmations(db, now)
            row = db.execute("SELECT * FROM requests WHERE state_hash=?", (request["state_hash"],)).fetchone()
            if not row or row["status"] != "exchanging" or row["expires"] <= now:
                raise OAuthError("STATE_INVALID")
            try:
                count = db.execute("""UPDATE accounts SET status='awaiting_confirmation',payload=?,subject_key=?,updated=?
                    WHERE owner=? AND generation=? AND status='authorizing'""",
                    (self.pack(payload), subject_key, now, row["owner"], row["generation"])).rowcount
            except sqlite3.IntegrityError:
                raise OAuthError("ACCOUNT_IN_USE") from None
            if count != 1:
                raise OAuthError("STATE_INVALID")
            db.execute("DELETE FROM requests WHERE state_hash=?", (row["state_hash"],))

    def fail_request(self, request, now):
        with self.connection() as db:
            db.execute("DELETE FROM requests WHERE state_hash=?", (request["state_hash"],))
            db.execute("""UPDATE accounts SET status='reauthorize',payload=?,subject_key=NULL,updated=?
                WHERE owner=? AND generation=? AND status='authorizing'""",
                (self.pack({}), now, request["owner"], request["generation"]))

    def confirm(self, owner, proof, now):
        with self.connection() as db:
            row = db.execute("SELECT * FROM accounts WHERE owner=?", (owner,)).fetchone()
            if not row or row["status"] != "awaiting_confirmation":
                raise OAuthError("CONFIRM_INVALID")
            value = self.unpack(row["payload"])
            if value["confirm_expires"] <= now or not hmac.compare_digest(value["confirm_hash"], hashlib.sha256(proof.encode()).hexdigest()):
                raise OAuthError("CONFIRM_INVALID")
            value.pop("confirm_hash"); value.pop("confirm_expires")
            value["binding_id"] = secrets.token_hex(16)
            db.execute("UPDATE accounts SET status='active',payload=?,updated=? WHERE owner=?",
                       (self.pack(value), now, owner))
            notice = db.execute("SELECT value FROM meta WHERE key=?", ("link_notice:" + owner,)).fetchone()
            db.execute("DELETE FROM meta WHERE key=?", ("link_notice:" + owner,))
            if notice:
                notice = self.unpack(notice["value"])
                if notice["generation"] == row["generation"]:
                    return notice

    def remember_link(self, owner, generation, group_id, message_id):
        """Only called with the Bot's successful native authorization receipt."""
        with self.connection() as db:
            row = db.execute("SELECT generation,status FROM accounts WHERE owner=?", (owner,)).fetchone()
            if not row or row["generation"] != generation or row["status"] not in {"authorizing", "awaiting_confirmation", "active"}:
                return
            notice = dict(generation=generation, group_id=group_id, message_id=message_id)
            if row["status"] == "active":
                return notice  # Confirmation raced ahead of the send receipt.
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("link_notice:" + owner, self.pack(notice)))

    def reserve_refresh(self, owner, generation, snapshot, now, reservation):
        with self.connection() as db:
            count = db.execute("""UPDATE accounts SET status='refreshing',updated=?,generation=?
                WHERE owner=? AND generation=? AND payload=? AND status='active'""", (now, reservation, owner, generation, snapshot)).rowcount
            if count != 1:
                raise OAuthError("BUSY")

    def save_refresh(self, owner, generation, status, value, now):
        with self.connection() as db:
            count = db.execute("""UPDATE accounts SET status=?,payload=?,updated=?,subject_key=CASE WHEN ?='reauthorize'
                THEN NULL ELSE subject_key END WHERE owner=? AND generation=? AND status='refreshing'""",
                (status, self.pack(value), now, status, owner, generation)).rowcount
            return count == 1

    def unlink(self, owner):
        with self.connection() as db:
            db.execute("DELETE FROM meta WHERE key=?", ("link_notice:" + owner,))
            db.execute("DELETE FROM requests WHERE owner=?", (owner,))
            db.execute("DELETE FROM accounts WHERE owner=?", (owner,))

    def invalidate(self, owner, token, now):
        with self.connection() as db:
            row = db.execute("SELECT * FROM accounts WHERE owner=?", (owner,)).fetchone()
            if row and row["status"] == "active" and hmac.compare_digest(self.unpack(row["payload"])["access_token"], token):
                db.execute("UPDATE accounts SET status='reauthorize',payload=?,subject_key=NULL,updated=? WHERE owner=?",
                           (self.pack({}), now, owner))
                return True
            return False

    def credential_lease(self, owner, fingerprint):
        with self.connection() as db:
            row = db.execute("SELECT * FROM accounts WHERE owner=?", (owner,)).fetchone()
            if not row or row["status"] != "active":
                raise OAuthError("NOT_AUTHORIZED")
            value = self.unpack(row["payload"])
            if value["config"] != fingerprint:
                raise OAuthError("NOT_CONFIGURED")
            # Upgrade existing phase 3A grants in place, without reading legacy scores.
            if not value.get("binding_id"):
                value["binding_id"] = secrets.token_hex(16)
                db.execute("UPDATE accounts SET payload=? WHERE owner=?", (self.pack(value), owner))
            return value["access_token"], value["binding_id"]

    def binding_matches(self, owner, binding, fingerprint):
        row = self.get(owner)
        if not row or row["status"] not in {"active", "refreshing"}:
            return False
        value = self.unpack(row["payload"])
        return bool(binding and value.get("binding_id") == binding and value.get("config") == fingerprint)

    def current_binding(self, owner, fingerprint):
        row = self.get(owner)
        if not row or row["status"] not in {"active", "refreshing"}:
            return None
        value = self.unpack(row["payload"])
        return value.get("binding_id") if value.get("config") == fingerprint else None
