import socket,time
s=socket.socket();s.bind(("127.0.0.1",47102));s.listen(5)
while True:
    c,_=s.accept();c.close()
