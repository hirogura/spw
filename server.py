#!/usr/bin/env python3
import os, json, hashlib, hmac, secrets, tempfile, shutil, subprocess, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime

PORT      = int(os.environ.get('PORT', 3345))
BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / 'data'
PW_ZIP    = DATA_DIR / 'spw.zip'
CFG_FILE  = DATA_DIR / 'config.json'
BACKUP_DIR= DATA_DIR / 'backups'
PUB_DIR   = BASE_DIR / 'public'

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

sessions = {}  # token -> password
sessions_lock = threading.Lock()

MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.json': 'application/json',
  '.ico':  'image/x-icon',
  '.svg':  'image/svg+xml',
}

# ---------- config ----------
def load_config():
  try:
    return json.loads(CFG_FILE.read_text())
  except:
    return {}

def save_config(cfg):
  CFG_FILE.write_text(json.dumps(cfg, indent=2))

# ---------- password hash ----------
def hash_password(password):
  salt = secrets.token_hex(16)
  dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64)
  return {'salt': salt, 'hash': dk.hex()}

def verify_password(password, stored):
  dk = hashlib.scrypt(password.encode(), salt=stored['salt'].encode(), n=16384, r=8, p=1, dklen=64)
  return hmac.compare_digest(dk.hex(), stored['hash'])

# ---------- zip ----------
def save_encrypted(data, password):
  tmp = Path(tempfile.mkdtemp())
  try:
    jf = tmp / 'passwords.json'
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if PW_ZIP.exists():
      PW_ZIP.unlink()
    subprocess.run(
      ['7z', 'a', '-tzip', '-mem=AES256', f'-p{password}', str(PW_ZIP), str(jf)],
      check=True, capture_output=True, timeout=30
    )
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

def load_encrypted(password):
  if not PW_ZIP.exists():
    return {'categories': []}
  tmp = Path(tempfile.mkdtemp())
  try:
    subprocess.run(
      ['7z', 'x', f'-p{password}', f'-o{tmp}', str(PW_ZIP), '-y'],
      check=True, capture_output=True, timeout=30
    )
    jf = tmp / 'passwords.json'
    return json.loads(jf.read_text()) if jf.exists() else {'categories': []}
  except:
    return {'categories': []}
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- HTTP handler ----------
class Handler(BaseHTTPRequestHandler):
  def log_message(self, fmt, *args):
    pass  # アクセスログ抑制

  def send_json(self, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode()
    self.send_response(code)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def send_bytes(self, code, body, content_type, filename=None):
    self.send_response(code)
    self.send_header('Content-Type', content_type)
    self.send_header('Content-Length', str(len(body)))
    if filename:
      self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
    self.end_headers()
    self.wfile.write(body)

  def auth_token(self):
    return self.headers.get('x-auth-token', '')

  def get_session_password(self):
    token = self.auth_token()
    with sessions_lock:
      s = sessions.get(token)
    return s['password'] if s else None

  def read_json(self):
    length = int(self.headers.get('Content-Length', 0))
    return json.loads(self.rfile.read(length)) if length else {}

  def serve_static(self, path):
    p = PUB_DIR / path.lstrip('/')
    if not p.exists() or not p.is_file():
      p = PUB_DIR / 'index.html'
    ext = p.suffix.lower()
    mime = MIME.get(ext, 'application/octet-stream')
    body = p.read_bytes()
    self.send_bytes(200, body, mime)

  def do_GET(self):
    parsed = urlparse(self.path)
    path = parsed.path

    if path == '/api/auth/status':
      cfg = load_config()
      self.send_json(200, {'hasPassword': bool(cfg.get('passwordHash'))})

    elif path == '/api/passwords':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'})
        return
      self.send_json(200, load_encrypted(pw))

    else:
      self.serve_static(path if path != '/' else '/index.html')

  def do_POST(self):
    parsed = urlparse(self.path)
    path = parsed.path

    if path == '/api/auth/setup':
      cfg = load_config()
      if cfg.get('passwordHash'):
        self.send_json(400, {'error': 'Password already set'}); return
      body = self.read_json()
      pw = body.get('password', '')
      if len(pw) < 4:
        self.send_json(400, {'error': 'Password must be at least 4 characters'}); return
      cfg['passwordHash'] = hash_password(pw)
      save_config(cfg)
      token = secrets.token_hex(32)
      with sessions_lock:
        sessions[token] = {'password': pw}
      self.send_json(200, {'success': True, 'token': token})

    elif path == '/api/auth/login':
      cfg = load_config()
      if not cfg.get('passwordHash'):
        self.send_json(400, {'error': 'No password set'}); return
      body = self.read_json()
      pw = body.get('password', '')
      if not pw:
        self.send_json(400, {'error': 'Password required'}); return
      if not verify_password(pw, cfg['passwordHash']):
        self.send_json(401, {'error': 'Wrong password'}); return
      token = secrets.token_hex(32)
      with sessions_lock:
        sessions[token] = {'password': pw}
      self.send_json(200, {'success': True, 'token': token})

    elif path == '/api/auth/logout':
      token = self.auth_token()
      with sessions_lock:
        sessions.pop(token, None)
      self.send_json(200, {'success': True})

    elif path == '/api/auth/change-password':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      body = self.read_json()
      cur = body.get('currentPassword', '')
      new = body.get('newPassword', '')
      cfg = load_config()
      if not verify_password(cur, cfg['passwordHash']):
        self.send_json(401, {'error': 'Current password is wrong'}); return
      if len(new) < 4:
        self.send_json(400, {'error': 'New password must be at least 4 characters'}); return
      data = load_encrypted(pw)
      cfg['passwordHash'] = hash_password(new)
      save_config(cfg)
      save_encrypted(data, new)
      token = self.auth_token()
      with sessions_lock:
        if token in sessions:
          sessions[token]['password'] = new
      self.send_json(200, {'success': True})

    elif path == '/api/passwords':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      body = self.read_json()
      save_encrypted(body, pw)
      self.send_json(200, {'success': True})

    elif path == '/api/export':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      data = load_encrypted(pw)
      tmp = Path(tempfile.mkdtemp())
      try:
        jf = tmp / 'passwords.json'
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
        filename = f'passwords-{ts}.zip'
        zippath = tmp / filename
        subprocess.run(
          ['7z', 'a', '-tzip', '-mem=AES256', f'-p{pw}', str(zippath), str(jf)],
          check=True, capture_output=True, timeout=30
        )
        body = zippath.read_bytes()
        self.send_bytes(200, body, 'application/zip', filename)
      except Exception as e:
        self.send_json(500, {'error': str(e)})
      finally:
        shutil.rmtree(tmp, ignore_errors=True)

    elif path == '/api/import':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      body = self.read_json()
      file_pw = body.get('password', '')
      file_data = body.get('fileData', '')
      if not file_pw or not file_data:
        self.send_json(400, {'error': 'Password and file required'}); return
      import base64
      tmp = Path(tempfile.mkdtemp())
      try:
        arc = tmp / 'import.zip'
        arc.write_bytes(base64.b64decode(file_data))
        out = tmp / 'out'
        out.mkdir()
        subprocess.run(
          ['7z', 'x', f'-p{file_pw}', f'-o{out}', str(arc), '-y'],
          check=True, capture_output=True, timeout=30
        )
        jf = out / 'passwords.json'
        if not jf.exists():
          self.send_json(400, {'error': 'passwords.json not found in archive'}); return
        imported = json.loads(jf.read_text())
        save_encrypted(imported, pw)
        self.send_json(200, {'success': True})
      except Exception:
        self.send_json(500, {'error': 'Wrong password or corrupt file'})
      finally:
        shutil.rmtree(tmp, ignore_errors=True)

    else:
      self.send_json(404, {'error': 'Not found'})

from http.server import ThreadingHTTPServer
print(f'SPW Password Manager running on http://0.0.0.0:{PORT}')
server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
server.serve_forever()
