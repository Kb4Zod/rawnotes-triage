"""A very small Chrome DevTools Protocol client over the standard library, for the browser regression tests.
Needs a Chromium/Chrome binary (CHROMIUM env var, or chromium / google-chrome on PATH); tests skip without one.
Not part of the app: nothing in server.py imports this."""
import base64, json, os, shutil, socket, struct, subprocess, tempfile, threading, time, urllib.request, queue


def chromium_path():
    for c in (os.environ.get("CHROMIUM"), "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if c and shutil.which(c): return shutil.which(c)
    return None


class WS:
    def __init__(self, url):
        host, rest = url[5:].split("/", 1); h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf: buf += self.s.recv(1)
        if b" 101 " not in buf.split(b"\r\n")[0]: raise RuntimeError("websocket handshake failed: " + buf.decode(errors="replace"))
        self.lock = threading.Lock()

    def send(self, text):
        data = text.encode(); n = len(data); mask = os.urandom(4)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        with self.lock: self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk: raise ConnectionError("browser closed the connection")
            out += chunk
        return out

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2); op = b1 & 0x0F; n = b2 & 0x7F
            if n == 126: n = struct.unpack(">H", self._read(2))[0]
            elif n == 127: n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if op == 8: raise ConnectionError("closed")
            if op in (1, 0): msg += payload
            if b1 & 0x80 and op in (1, 0): return msg.decode()


KEYS = {  # key -> (code, windowsVirtualKeyCode, text)
    "Enter": ("Enter", 13, "\r"), "Escape": ("Escape", 27, ""), "Tab": ("Tab", 9, ""), " ": ("Space", 32, " "),
    "ArrowDown": ("ArrowDown", 40, ""), "ArrowUp": ("ArrowUp", 38, ""), "ArrowLeft": ("ArrowLeft", 37, ""), "ArrowRight": ("ArrowRight", 39, ""), "Backspace": ("Backspace", 8, ""),
}


class Browser:
    def __init__(self, headless=True):
        self.dir = tempfile.mkdtemp(prefix="rt-chrome-")
        args = [chromium_path(), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-extensions",
                "--remote-debugging-port=0", f"--user-data-dir={self.dir}", "--disable-background-timer-throttling", "about:blank"]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        f = os.path.join(self.dir, "DevToolsActivePort")
        for _ in range(200):
            if os.path.exists(f) and len(open(f).read().split("\n")) >= 2: break
            time.sleep(0.05)
        port, path = open(f).read().split("\n")[:2]
        self.ws = WS(f"ws://127.0.0.1:{port}{path}")
        self.next_id = 0; self.results = {}; self.events = queue.Queue(); self.cv = threading.Condition()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            while True:
                m = json.loads(self.ws.recv())
                with self.cv:
                    if "id" in m: self.results[m["id"]] = m; self.cv.notify_all()
                    else: self.events.put(m)
        except Exception: pass

    def call(self, method, params=None, session=None, timeout=30):
        with self.cv: self.next_id += 1; i = self.next_id
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}, **({"sessionId": session} if session else {})}))
        end = time.time() + timeout
        with self.cv:
            while i not in self.results:
                if time.time() > end: raise TimeoutError(method)
                self.cv.wait(0.1)
            m = self.results.pop(i)
        if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
        return m.get("result", {})

    def page(self):
        tid = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = self.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        return Page(self, tid, sid)

    def close(self):
        try: self.call("Browser.close", timeout=5)
        except Exception: pass
        try: self.proc.wait(5)
        except Exception: self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


class Page:
    def __init__(self, br, tid, sid):
        self.br, self.tid, self.sid = br, tid, sid
        for d in ("Page", "Runtime"): self.cmd(f"{d}.enable")
        self.errors = []

    def add_init_script(self, js): self.cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})

    def collect_errors(self):
        """Uncaught page errors seen so far (events are routed per page)."""
        while True:
            try: m = self.br.events.get_nowait()
            except queue.Empty: break
            if m.get("sessionId") == self.sid and m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]; self.errors.append(d.get("exception", {}).get("description") or d.get("text", ""))
        return list(self.errors)

    def cmd(self, method, params=None, timeout=30): return self.br.call(method, params, self.sid, timeout)

    def goto(self, url, wait_for="document.readyState==='complete'"):
        try: before = self.eval("performance.timeOrigin")
        except RuntimeError: before = None
        self.cmd("Page.navigate", {"url": url})
        # right after navigate the OLD document still answers; wait for the new one (a new timeOrigin) before trusting any check
        if before is not None: self.wait(f"performance.timeOrigin!=={before}")
        self.wait(wait_for)

    def eval(self, js, timeout=30):
        r = self.cmd("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True, "userGesture": True}, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]; raise RuntimeError("page error: " + (d.get("exception", {}).get("description") or d.get("text", "")))
        return r["result"].get("value")

    def wait(self, js, timeout=10):
        end = time.time() + timeout; last = None
        while time.time() < end:
            try:
                last = self.eval(f"!!({js})")
                if last: return True
            except RuntimeError: pass
            time.sleep(0.05)
        raise TimeoutError(f"condition never became true: {js} (last: {last!r})")

    def resize(self, w, h, mobile=False, dark=False):
        self.cmd("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})
        self.cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}]})

    def key(self, key, ctrl=False, shift=False):
        code, vk, text = KEYS.get(key, (("Key" + key.upper()) if len(key) == 1 and key.isalpha() else key, ord(key.upper()) if len(key) == 1 else 0, key if len(key) == 1 else ""))
        mods = (2 if ctrl else 0) | (8 if shift else 0)
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": mods}
        self.cmd("Input.dispatchKeyEvent", {**base, "type": "keyDown", **({"text": text} if text and not ctrl else {})})
        self.cmd("Input.dispatchKeyEvent", {**base, "type": "keyUp"})

    def type(self, text):
        for ch in text: self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch}); self.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})

    def click(self, selector):
        probe = f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return null;e.scrollIntoView({{block:'center'}});const b=e.getBoundingClientRect();return [b.x+b.width/2,b.y+b.height/2]}})()"
        r = self.eval(probe)
        if not r: raise LookupError("no element " + selector)
        for _ in range(20):  # wait for layout to stop moving (a panel loading above the target shifts it), as a person would
            time.sleep(0.05); again = self.eval(probe)
            if again == r: break
            r = again
            if not r: raise LookupError("no element " + selector)
        for t in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent", {"type": t, "x": r[0], "y": r[1], "button": "left", "clickCount": 1})

    def screenshot(self, path):
        with open(path, "wb") as fh: fh.write(base64.b64decode(self.cmd("Page.captureScreenshot", {"format": "png"})["data"]))

    def close(self):
        try: self.br.call("Target.closeTarget", {"targetId": self.tid})
        except Exception: pass
