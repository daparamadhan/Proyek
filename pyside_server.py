import socket
import threading
import os
import sys
import json
import shutil
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                               QWidget, QPushButton, QLabel, QTextEdit, QFrame, 
                               QStatusBar, QGroupBox, QGridLayout)
from PySide6.QtCore import QTimer, Signal, QObject, Qt
from PySide6.QtGui import QFont, QIcon
import http.server
import socketserver

class ServerSignals(QObject):
    log_message = Signal(str, str)  # message, type
    client_count_changed = Signal(int)
    status_changed = Signal(str, str)  # status, color

class PySideServer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🖥️ Mini Drive LAN Server")
        self.showMaximized()
        self.storage = "storage"
        os.makedirs(self.storage, exist_ok=True)
        
        self.running = False
        self.server = None
        self.clients = []
        
        self.signals = ServerSignals()
        self.signals.log_message.connect(self.add_log_message)
        self.signals.client_count_changed.connect(self.update_client_count)
        self.signals.status_changed.connect(self.update_status)
        
        self.httpd = None
        self.apply_styles()
        self.setup_ui()
        self.setup_timer()

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f6fa;
            }
            QWidget {
                color: #2f3640;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #dcdde1;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #3498db;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            #danger_btn {
                background-color: #e84118;
            }
            #danger_btn:hover {
                background-color: #c23616;
            }
            #success_btn {
                background-color: #4cd137;
            }
            #success_btn:hover {
                background-color: #44bd32;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #dcdde1;
                border-radius: 4px;
                color: #2f3640;
                font-family: 'Consolas', monospace;
                font-size: 12px;
                padding: 10px;
            }
            QLabel#header_title {
                color: #2f3640;
                font-size: 32px;
                font-weight: bold;
            }
            QLabel#status_panel {
                background-color: #ffffff;
                padding: 15px;
                border-radius: 8px;
                font-size: 18px;
                border: 1px solid #dcdde1;
            }
        """)

    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)
        self.update_time()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(25)
        
        # Header
        header = QHBoxLayout()
        title = QLabel("Mini Drive Server")
        title.setObjectName("header_title")
        header.addWidget(title)
        header.addStretch()
        
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color: #666; font-size: 14px;")
        header.addWidget(self.time_label)
        layout.addLayout(header)
        
        # Status & Control Area
        ctrl_panel = QHBoxLayout()
        
        self.status_label = QLabel("● Server Stopped")
        self.status_label.setObjectName("status_panel")
        self.status_label.setStyleSheet("color: #e74c3c;")
        
        self.client_label = QLabel("Active Clients: 0")
        self.client_label.setStyleSheet("font-size: 16px; color: #888;")
        
        self.start_btn = QPushButton("Start Server")
        self.start_btn.setObjectName("success_btn")
        self.start_btn.setFixedWidth(200)
        self.start_btn.clicked.connect(self.toggle_server)
        
        ctrl_panel.addWidget(self.status_label)
        ctrl_panel.addStretch()
        ctrl_panel.addWidget(self.client_label)
        ctrl_panel.addWidget(self.start_btn)
        layout.addLayout(ctrl_panel)
        
        # Storage Info
        info_group = QGroupBox("Storage Information")
        info_layout = QGridLayout(info_group)
        info_layout.setContentsMargins(20, 25, 20, 20)
        
        storage_path = os.path.abspath(self.storage)
        info_layout.addWidget(QLabel("Root Path:"), 0, 0)
        info_layout.addWidget(QLabel(storage_path), 0, 1)
        info_layout.addWidget(QLabel("Port:"), 1, 0)
        info_layout.addWidget(QLabel("5555 (TCP)"), 1, 1)
        layout.addWidget(info_group)
        
        # Log Area
        log_group = QGroupBox("Activity Monitor")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(20, 25, 20, 20)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #f8f9fa; color: #2c3e50;
                border: 1px solid #e0e0e0; border-radius: 6px;
                font-family: 'Consolas', 'Monaco', monospace; font-size: 12px;
                padding: 15px; line-height: 1.5;
            }
            QScrollBar:vertical {
                background-color: #f8f9fa; width: 8px; border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #bdc3c7; border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #95a5a6;
            }
        """)
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        
        layout.addWidget(log_group)
        
        # Status Bar
        self.statusBar().setStyleSheet("""
            QStatusBar {
                background-color: #ffffff; color: #7f8c8d;
                border-top: 1px solid #e0e0e0; font-family: 'Segoe UI'; font-size: 12px;
            }
        """)
        self.statusBar().showMessage("Ready to start server")
        
    def update_time(self):
        self.time_label.setText(datetime.now().strftime("🕒 %Y-%m-%d %H:%M:%S"))
        
    def add_log_message(self, message, msg_type="info"):
        color = {"success": "#27ae60", "error": "#e74c3c", "warning": "#f39c12", "client": "#9b59b6"}.get(msg_type, "#3498db")
        self.log_text.append(f'<span style="color: {color};">[{datetime.now().strftime("%H:%M:%S")}] {message}</span>')
        
    def update_client_count(self, count):
        self.client_label.setText(f"Active Clients: {count}")
        
    def update_status(self, status, color):
        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"color: {color}; background-color: #f1f2f6; padding: 15px; border-radius: 8px; font-size: 18px; border: 1px solid #dcdde1;")
        
    def toggle_server(self):
        if self.running:
            self.stop_server()
        else:
            self.start_server()
            
    def start_server(self):
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(("0.0.0.0", 5555))
            self.server.listen()
            
            self.running = True
            self.signals.status_changed.emit("🟢 Server Running", "#2ecc71")
            self.start_btn.setText("Stop Server")
            self.start_btn.setObjectName("danger_btn")
            self.start_btn.setStyle(self.start_btn.style()) 
            
            threading.Thread(target=self.accept_clients, daemon=True).start()
            self.start_http_server()
            self.signals.log_message.emit("🚀 Server started on port 5555", "success")
            self.statusBar().showMessage("Server running on port 5555")
            
        except Exception as e:
            self.signals.log_message.emit(f"❌ Failed to start: {e}", "error")

    def stop_server(self):
        self.running = False
        if self.server:
            try:
                for conn in self.clients:
                    try: conn.close()
                    except: pass
                self.server.close()
            except: pass
            
        self.signals.status_changed.emit("● Server Stopped", "#e74c3c")
        self.start_btn.setText("Start Server")
        self.start_btn.setObjectName("success_btn")
        self.start_btn.setStyle(self.start_btn.style())
        self.signals.log_message.emit("🛑 Server stopped", "warning")
        self.statusBar().showMessage("Server stopped")
        self.signals.client_count_changed.emit(0)
        self.stop_http_server()

    def start_http_server(self):
        self_server = self
        storage_path = os.path.abspath("storage")
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)

        # Get LAN IP for logging
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except:
            lan_ip = "localhost"

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=storage_path, **kwargs)
            
            def log_message(self, format, *args):
                nonlocal self_server
                self_server.signals.log_message.emit(f"📱 Mobile Request: {args[0]}", "client")

        def run_server():
            nonlocal self
            try:
                socketserver.TCPServer.allow_reuse_address = True
                with socketserver.TCPServer(("", 9000), Handler) as httpd:
                    self.httpd = httpd
                    self.signals.log_message.emit(f"🌐 Web Share active at http://{lan_ip}:9000", "success")
                    httpd.serve_forever()
            except Exception as e:
                self.signals.log_message.emit(f"⚠️ Web Share failed: {e}", "warning")

        threading.Thread(target=run_server, daemon=True).start()

    def stop_http_server(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except: pass
            self.httpd = None
        
    def accept_clients(self):
        while self.running:
            try:
                conn, addr = self.server.accept()
                self.clients.append(conn)
                self.signals.client_count_changed.emit(len(self.clients))
                self.signals.log_message.emit(f"📱 Client connected: {addr[0]}:{addr[1]}", "client")
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except:
                if self.running:
                    self.signals.log_message.emit("❌ Accept error", "error")
                break
                
    def send_json(self, conn, data):
        """Helper to send JSON data with newline delimiter"""
        try:
            msg = json.dumps(data) + "\n"
            conn.sendall(msg.encode())
        except Exception as e:
            self.signals.log_message.emit(f"Send error: {e}", "error")

    def handle_client(self, conn, addr):
        buffer = ""
        try:
            conn.settimeout(300)  # Longer timeout for persistent connection
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            while self.running:
                try:
                    data = conn.recv(1024).decode()
                    if not data:
                        break
                    
                    buffer += data
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not line.strip():
                            continue
                            
                        request = json.loads(line)
                        cmd = request.get("command")
                        
                        self.signals.log_message.emit(f"Command from {addr[0]}: {cmd}", "info")
                        
                        if cmd == "LIST":
                            self.handle_list(conn, request)
                        elif cmd == "UPLOAD":
                            self.handle_upload(conn, request, addr)
                        elif cmd == "DOWNLOAD":
                            self.handle_download(conn, request, addr)
                        elif cmd == "DELETE":
                            self.handle_delete(conn, request, addr)
                        elif cmd == "MKDIR":
                            self.handle_mkdir(conn, request, addr)
                        else:
                            self.send_json(conn, {"status": "error", "message": "Unknown command"})
                            
                except json.JSONDecodeError:
                    self.signals.log_message.emit(f"Invalid JSON from {addr[0]}", "error")
                    break
                except socket.timeout:
                    self.signals.log_message.emit(f"Timeout: {addr[0]}", "warning")
                    break
                except ConnectionResetError:
                    break
                except Exception as e:
                    self.signals.log_message.emit(f"Command processing error: {e}", "error")
                    break
                    
        except Exception as e:
            self.signals.log_message.emit(f"❌ Client error: {e}", "error")
        finally:
            try:
                conn.close()
                if conn in self.clients:
                    self.clients.remove(conn)
                self.signals.client_count_changed.emit(len(self.clients))
            except:
                pass
            self.signals.log_message.emit(f"📱 Client disconnected: {addr[0]}", "client")
            
    def get_full_path(self, relative_path):
        """Helper to safely get absolute path within storage"""
        base_path = os.path.abspath(self.storage)
        full_path = os.path.abspath(os.path.join(base_path, relative_path.lstrip("/")))
        if not full_path.startswith(base_path):
            raise Exception("Access denied: Path outside storage")
        return full_path

    def handle_list(self, conn, request):
        try:
            rel_path = request.get("path", "")
            full_path = self.get_full_path(rel_path)
            
            if not os.path.exists(full_path):
                os.makedirs(full_path)
                
            items = []
            for name in os.listdir(full_path):
                item_full_path = os.path.join(full_path, name)
                is_dir = os.path.isdir(item_full_path)
                size = os.path.getsize(item_full_path) if not is_dir else 0
                items.append({
                    "name": name,
                    "is_dir": is_dir,
                    "size": size,
                    "mtime": os.path.getmtime(item_full_path)
                })
                
            self.send_json(conn, {
                "status": "success",
                "items": items,
                "current_path": rel_path
            })
            self.signals.log_message.emit(f"📁 Listed {len(items)} items in {rel_path or 'root'}", "success")
            
        except Exception as e:
            self.signals.log_message.emit(f"❌ List error: {e}", "error")
            self.send_json(conn, {"status": "error", "message": str(e)})

    def handle_upload(self, conn, request, addr):
        try:
            filename = request.get("filename")
            size = request.get("size")
            rel_path = request.get("path", "")
            
            if not filename or size is None:
                return self.send_json(conn, {"status": "error", "message": "Missing info"})
                
            filepath = os.path.join(self.get_full_path(rel_path), filename)
            
            self.signals.log_message.emit(f"📤 Receiving: {filename} ({size} bytes)", "info")
            
            # Acknowledge and start receiving
            self.send_json(conn, {"status": "ready"})
            
            with open(filepath, "wb") as f:
                received = 0
                while received < size:
                    chunk = conn.recv(min(8192, size - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    
            if received == size:
                self.signals.log_message.emit(f"✅ Upload complete: {filename}", "success")
                self.send_json(conn, {"status": "success", "message": "Upload complete"})
            else:
                self.signals.log_message.emit(f"❌ Upload incomplete: {filename}", "error")
                if os.path.exists(filepath):
                    os.remove(filepath)
                self.send_json(conn, {"status": "error", "message": "Transfer incomplete"})
                    
        except Exception as e:
            self.signals.log_message.emit(f"❌ Upload error: {e}", "error")
            self.send_json(conn, {"status": "error", "message": str(e)})

    def handle_download(self, conn, request, addr):
        try:
            filename = request.get("filename")
            rel_path = request.get("path", "")
            filepath = os.path.join(self.get_full_path(rel_path), filename)
            
            if os.path.exists(filepath) and os.path.isfile(filepath):
                size = os.path.getsize(filepath)
                self.send_json(conn, {"status": "success", "size": size})
                
                with open(filepath, "rb") as f:
                    sent = 0
                    while sent < size:
                        chunk = f.read(8192)
                        if not chunk: break
                        conn.sendall(chunk)
                        sent += len(chunk)
                        
                self.signals.log_message.emit(f"📥 Download: {filename} to {addr[0]}", "success")
            else:
                self.send_json(conn, {"status": "error", "message": "File not found"})
                self.signals.log_message.emit(f"❌ Download failed: {filename} not found", "error")
                
        except Exception as e:
            self.signals.log_message.emit(f"❌ Download error: {e}", "error")
            self.send_json(conn, {"status": "error", "message": str(e)})

    def handle_delete(self, conn, request, addr):
        try:
            filename = request.get("filename")
            rel_path = request.get("path", "")
            filepath = os.path.join(self.get_full_path(rel_path), filename)
            
            if os.path.exists(filepath):
                if os.path.isdir(filepath):
                    shutil.rmtree(filepath)
                else:
                    os.remove(filepath)
                self.send_json(conn, {"status": "success", "message": f"Deleted {filename}"})
                self.signals.log_message.emit(f"🗑️ Deleted: {filename}", "success")
            else:
                self.send_json(conn, {"status": "error", "message": "Not found"})
                
        except Exception as e:
            self.signals.log_message.emit(f"❌ Delete error: {e}", "error")
            self.send_json(conn, {"status": "error", "message": str(e)})

    def handle_mkdir(self, conn, request, addr):
        try:
            dirname = request.get("dirname")
            rel_path = request.get("path", "")
            new_dir = os.path.join(self.get_full_path(rel_path), dirname)
            
            os.makedirs(new_dir, exist_ok=True)
            self.send_json(conn, {"status": "success", "message": f"Folder {dirname} created"})
            self.signals.log_message.emit(f"📁 Created folder: {dirname}", "success")
        except Exception as e:
            self.send_json(conn, {"status": "error", "message": str(e)})

    def closeEvent(self, event):
        if self.running:
            self.stop_server()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look
    
    server = PySideServer()
    server.show()
    
    sys.exit(app.exec())