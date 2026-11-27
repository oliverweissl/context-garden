import socket, sys, os
allowed, other = int(sys.argv[1]), int(sys.argv[2])
def t(name, f):
    try:
        r = f(); print(f"{name}: ALLOWED {r if r else ''}", flush=True)
    except Exception as e:
        print(f"{name}: blocked ({type(e).__name__}: {e})", flush=True)
def tcp(h, p, fam=socket.AF_INET):
    s = socket.socket(fam, socket.SOCK_STREAM); s.settimeout(5); s.connect((h, p)); s.close()
t("tcp 127.0.0.1:allowed", lambda: tcp("127.0.0.1", allowed))
t("tcp 127.0.0.1:other", lambda: tcp("127.0.0.1", other))
t("tcp6 [::1]:allowed", lambda: tcp("::1", allowed, socket.AF_INET6))
t("tcp 1.1.1.1:443", lambda: tcp("1.1.1.1", 443))
t("tcp 1.1.1.1 on the allowed port number", lambda: tcp("1.1.1.1", allowed))
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
    s.sendto(b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01", ("1.1.1.1", 53))
    return len(s.recvfrom(512)[0])
t("udp dns 1.1.1.1:53", udp)
t("getaddrinfo example.com", lambda: socket.getaddrinfo("example.com", 443)[0][4])
def bind():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1); return s.getsockname()
t("tcp bind+listen 127.0.0.1", bind)
def bind_any():
    s = socket.socket(); s.bind(("0.0.0.0", 0)); s.listen(1); return s.getsockname()
t("tcp bind+listen 0.0.0.0", bind_any)
def unix():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "s"); a = socket.socket(socket.AF_UNIX); a.bind(p); a.listen(1)
    b = socket.socket(socket.AF_UNIX); b.connect(p); return "pair ok"
if hasattr(socket, "AF_UNIX"):
    t("unix socket pair", unix)
