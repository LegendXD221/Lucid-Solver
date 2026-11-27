#!/usr/bin/env python3
"""Minimal SOCKS5 proxy forwarder for authenticated upstream proxies.

Listens on 127.0.0.1:10800 as an unauthenticated SOCKS5 proxy and
forwards all connections to an authenticated upstream SOCKS5 proxy.

This is needed because Chromium doesn't support SOCKS5 authentication
natively — it can use an unauthenticated SOCKS5 proxy via Playwright's
proxy config, and this forwarder handles the upstream auth.

Usage:
  python3 socks5_forwarder.py &
  # Then use socks5://127.0.0.1:10800 as the proxy in Playwright
"""
import socket
import struct
import select
import threading
import signal
import sys

# Upstream SOCKS5 proxy with auth
UPSTREAM_HOST = "p.webshare.io"
UPSTREAM_PORT = 1080
UPSTREAM_USER = "slwrayne-rotate"
UPSTREAM_PASS = "0aotsm8crk2h"

# Local listener (no auth)
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 10800

BUFFER_SIZE = 65536
_running = True


def relay(src, dst):
    """Relay data between two sockets."""
    try:
        while _running:
            r, _, _ = select.select([src, dst], [], [], 1)
            if not r:
                continue
            for sock in r:
                data = sock.recv(BUFFER_SIZE)
                if not data:
                    return
                other = dst if sock is src else src
                try:
                    other.sendall(data)
                except:
                    return
    except:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass


def handle_client(client):
    """Handle a SOCKS5 client connection (no auth, forward to upstream)."""
    try:
        # SOCKS5 greeting from client (no auth)
        data = client.recv(256)
        if len(data) < 2 or data[0] != 5:
            client.close()
            return
        # Reply: no auth needed
        client.sendall(b"\x05\x00")

        # SOCKS5 connect request
        data = client.recv(256)
        if len(data) < 7 or data[0] != 5 or data[1] != 1:
            client.close()
            return

        # Parse target address
        atyp = data[3]
        if atyp == 1:  # IPv4
            dst_addr = socket.inet_ntoa(data[4:8])
            dst_port = struct.unpack("!H", data[8:10])[0]
        elif atyp == 3:  # Domain
            dlen = data[4]
            dst_addr = data[5:5+dlen].decode()
            dst_port = struct.unpack("!H", data[5+dlen:7+dlen])[0]
        elif atyp == 4:  # IPv6
            dst_addr = socket.inet_ntop(socket.AF_INET6, data[4:20])
            dst_port = struct.unpack("!H", data[20:22])[0]
        else:
            client.close()
            return

        # Connect to upstream SOCKS5 proxy
        upstream = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=10)

        # SOCKS5 greeting with username/password auth (0x02)
        upstream.sendall(b"\x05\x01\x02")
        resp = upstream.recv(2)
        if resp != b"\x05\x02":
            client.sendall(b"\x05\x01\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
            upstream.close()
            client.close()
            return

        # Send username/password
        user = UPSTREAM_USER.encode()
        pwd = UPSTREAM_PASS.encode()
        upstream.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
        resp = upstream.recv(2)
        if resp[1] != 0:
            client.sendall(b"\x05\x01\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
            upstream.close()
            client.close()
            return

        # Send CONNECT request to upstream
        if atyp == 1:
            upstream.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(dst_addr) + struct.pack("!H", dst_port))
        elif atyp == 3:
            upstream.sendall(b"\x05\x01\x00\x03" + bytes([len(dst_addr)]) + dst_addr.encode() + struct.pack("!H", dst_port))
        elif atyp == 4:
            upstream.sendall(b"\x05\x01\x00\x04" + socket.inet_pton(socket.AF_INET6, dst_addr) + struct.pack("!H", dst_port))

        resp = upstream.recv(256)
        if resp[1] != 0:
            client.sendall(b"\x05\x01\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
            upstream.close()
            client.close()
            return

        # Success — relay data
        client.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
        relay(client, upstream)

    except Exception:
        try: client.close()
        except: pass


def main():
    global _running
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LOCAL_HOST, LOCAL_PORT))
    server.listen(32)
    print(f"SOCKS5 forwarder listening on {LOCAL_HOST}:{LOCAL_PORT}", flush=True)
    print(f"  upstream: socks5://{UPSTREAM_USER}:***@{UPSTREAM_HOST}:{UPSTREAM_PORT}", flush=True)

    while _running:
        try:
            client, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(client,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except:
            continue

    server.close()


if __name__ == "__main__":
    main()
