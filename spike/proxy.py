import socket, threading, sys, select
log = open(sys.argv[2], "a", buffering=1)
def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 120)
            if not r: break
            for s in r:
                d = s.recv(65536)
                if not d: return
                (b if s is a else a).sendall(d)
    except Exception: pass
    finally:
        a.close(); b.close()
def handle(c):
    try:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = c.recv(4096)
            if not chunk: return
            data += chunk
        line = data.split(b"\r\n")[0].decode()
        method, target = line.split(" ")[:2]
        log.write(method + " " + target + "\n")
        if method == "CONNECT":
            host, port = target.rsplit(":", 1)
            u = socket.create_connection((host, int(port)), timeout=15)
            c.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            pipe(c, u)
        else:
            c.sendall(b"HTTP/1.1 501 x\r\n\r\n"); c.close()
    except Exception as e:
        log.write("ERR " + repr(e) + "\n"); c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(64)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
