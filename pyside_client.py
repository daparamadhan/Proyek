import socket
import os
import sys
import json
import time
import threading
from datetime import datetime
import urllib.parse
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                               QWidget, QPushButton, QLabel, QLineEdit, QTextEdit, 
                               QTreeWidget, QTreeWidgetItem, QFrame, QGroupBox, 
                               QGridLayout, QFileDialog, QMessageBox,
                               QFormLayout, QSplitter, QProgressBar, QInputDialog, QDialog)
from PySide6.QtCore import QTimer, Signal, QObject, Qt, QThread
from PySide6.QtGui import QFont, QIcon, QPixmap, QImage
import qrcode
import io

class NetworkWorker(QThread):
    log_message = Signal(str, str)
    connection_changed = Signal(bool)
    files_updated = Signal(list, str)
    progress_updated = Signal(int)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.sock = None
        self.connected = False
        self.server_ip = ""
        self.current_path = ""
        self.running = True
        self.buffer = ""
        self.socket_lock = threading.Lock()
        self.is_transferring = False

    def run(self):
        while self.running:
            if not self.connected and self.server_ip:
                self.do_connect()
            
            if self.connected and not self.is_transferring:
                with self.socket_lock:
                    try:
                        self.sock.setblocking(False)
                        try:
                            data = self.sock.recv(4096).decode()
                            if data:
                                self.buffer += data
                                self.process_buffer()
                        except (BlockingIOError, socket.timeout):
                            pass
                        except Exception as e:
                            self.do_disconnect(f"Receive error: {e}")
                    except Exception as e:
                        self.do_disconnect(f"Socket error: {e}")
            
            time.sleep(0.1)

    def do_connect(self):
        with self.socket_lock:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect((self.server_ip, 5555))
                self.connected = True
                self.connection_changed.emit(True)
                self.log_message.emit(f"Connected to {self.server_ip}", "success")
                self.send_json_unlocked({"command": "LIST", "path": ""})
            except Exception as e:
                self.server_ip = ""
                self.error_occurred.emit(f"Connection failed: {e}")

    def do_disconnect(self, reason="Disconnected"):
        with self.socket_lock:
            if self.connected:
                self.connected = False
                if self.sock:
                    try: self.sock.close()
                    except: pass
                self.connection_changed.emit(False)
                self.log_message.emit(reason, "warning")

    def send_json(self, data):
        with self.socket_lock:
            self.send_json_unlocked(data)

    def send_json_unlocked(self, data):
        if self.connected:
            try:
                msg = json.dumps(data) + "\n"
                self.sock.sendall(msg.encode())
            except Exception as e:
                self.do_disconnect(f"Send error: {e}")

    def process_buffer(self):
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if not line.strip(): continue
            try:
                resp = json.loads(line)
                status = resp.get("status")
                
                if "items" in resp:
                    self.files_updated.emit(resp["items"], resp.get("current_path", ""))
                
                if status == "error":
                    self.error_occurred.emit(resp.get("message", "Unknown error"))
                elif status == "success" and "message" in resp:
                    self.log_message.emit(resp["message"], "success")
            except Exception as e:
                print(f"JSON Parse error: {e}")

    def upload_file(self, file_path, target_path):
        if not self.connected: return
        self.is_transferring = True
        try:
            with self.socket_lock:
                self.buffer = "" # Clear buffer for fresh command
                filename = os.path.basename(file_path)
                size = os.path.getsize(file_path)
                self.send_json_unlocked({"command": "UPLOAD", "filename": filename, "size": size, "path": target_path})
                
                # Wait for 'ready' response
                self.sock.setblocking(True)
                self.sock.settimeout(10)
                
                # Manual line reading to avoid stealing other messages
                ready_data = b""
                while b"\n" not in ready_data:
                    char = self.sock.recv(1)
                    if not char: break
                    ready_data += char
                
                resp = json.loads(ready_data.decode())
                if resp.get("status") != "ready":
                    raise Exception(f"Unexpected response: {resp}")

                with open(file_path, "rb") as f:
                    sent = 0
                    while sent < size:
                        chunk = f.read(8192)
                        if not chunk: break
                        self.sock.sendall(chunk)
                        sent += len(chunk)
                        self.progress_updated.emit(int((sent / size) * 100))
                
                self.send_json_unlocked({"command": "LIST", "path": target_path})
        except Exception as e:
            self.error_occurred.emit(f"Upload failed: {e}")
        finally:
            self.is_transferring = False

    def download_file(self, filename, target_path, save_path):
        if not self.connected: return
        self.is_transferring = True
        try:
            with self.socket_lock:
                self.buffer = "" # Clear buffer
                self.send_json_unlocked({"command": "DOWNLOAD", "filename": filename, "path": target_path})
                
                self.sock.setblocking(True)
                self.sock.settimeout(10)
                
                # Read response line
                resp_data = b""
                while b"\n" not in resp_data:
                    char = self.sock.recv(1)
                    if not char: break
                    resp_data += char
                
                resp = json.loads(resp_data.decode())
                if resp.get("status") == "success":
                    size = resp.get("size")
                else:
                    raise Exception(resp.get("message", "File not found"))
                
                with open(save_path, "wb") as f:
                    received = 0
                    while received < size:
                        chunk = self.sock.recv(min(8192, size - received))
                        if not chunk: break
                        f.write(chunk)
                        received += len(chunk)
                        self.progress_updated.emit(int((received / size) * 100))
                
                self.log_message.emit(f"Downloaded {filename}", "success")
        except Exception as e:
            self.error_occurred.emit(f"Download failed: {e}")
        finally:
            self.is_transferring = False

class QRDialog(QDialog):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📱 Mobile Share")
        self.setFixedSize(350, 450)
        self.setStyleSheet("background-color: #ffffff; color: #2f3640;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)
        
        info = QLabel("Scan to download on mobile:")
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(info)
        
        # Generate QR
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to QPixmap
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        qimage = QImage.fromData(buffer.getvalue())
        pixmap = QPixmap.fromImage(qimage)
        
        self.qr_label = QLabel()
        self.qr_label.setPixmap(pixmap.scaled(250, 250, Qt.KeepAspectRatio))
        self.qr_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.qr_label)
        
        url_label = QLabel(url)
        url_label.setWordWrap(True)
        url_label.setAlignment(Qt.AlignCenter)
        url_label.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(url_label)
        
        tip = QLabel("💡 Tip: Jika browser HP 'Loading' terus, pastikan Firewall di laptop Anda sudah mengizinkan koneksi.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #e67e22; font-size: 10px; font-style: italic;")
        layout.addWidget(tip)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

class PySideClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📁 Mini Google Drive LAN Client")
        self.showMaximized()
        self.username = "Guest"
        self.current_path = ""
        
        self.worker = NetworkWorker()
        self.worker.log_message.connect(self.add_log_message)
        self.worker.connection_changed.connect(self.update_connection_status)
        self.worker.files_updated.connect(self.update_file_tree)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.error_occurred.connect(self.show_error)
        self.worker.start()
        
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
                padding: 8px 16px;
                font-weight: bold;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #1c5980;
            }
            QPushButton:disabled {
                background-color: #dcdde1;
                color: #7f8c8d;
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
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #dcdde1;
                border-radius: 4px;
                padding: 6px;
                color: #2f3640;
            }
            QLineEdit:focus {
                border: 1px solid #3498db;
            }
            QTreeWidget {
                background-color: #ffffff;
                border: 1px solid #dcdde1;
                border-radius: 4px;
                alternate-background-color: #f5f6fa;
                outline: none;
            }
            QTreeWidget::item {
                padding: 8px;
                border-bottom: 1px solid #f1f2f6;
            }
            QTreeWidget::item:selected {
                background-color: #3498db;
                color: white;
            }
            QHeaderView::section {
                background-color: #f1f2f6;
                color: #2f3640;
                padding: 6px;
                border: none;
                font-weight: bold;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #dcdde1;
                border-radius: 4px;
                color: #2f3640;
            }
            QProgressBar {
                border: 1px solid #dcdde1;
                border-radius: 10px;
                text-align: center;
                background-color: #f5f6fa;
                height: 20px;
                color: #2f3640;
            }
            QProgressBar::chunk {
                background-color: #3498db;
                border-radius: 9px;
            }
            QLabel#header_title {
                color: #3498db;
                font-size: 28px;
                font-weight: bold;
            }
            QLabel#path_text {
                color: #2f3640;
                font-size: 14px;
                font-family: 'Consolas', monospace;
                background-color: #f1f2f6;
                padding: 4px 10px;
                border-radius: 4px;
                border: 1px solid #dcdde1;
            }
        """)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        # Header
        header = QFrame()
        header.setObjectName("header_panel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 10)
        
        title = QLabel(f"Mini Drive")
        title.setObjectName("header_title")
        
        user_info = QLabel(f"👤 {self.username}")
        user_info.setStyleSheet("font-size: 16px; color: #888; font-weight: bold;")
        
        header_layout.addWidget(title)
        header_layout.addWidget(user_info)
        header_layout.addStretch()
        
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color: #666; font-size: 14px;")
        header_layout.addWidget(self.time_label)
        layout.addWidget(header)
        
        # Connection Panel
        conn_group = QGroupBox("Network Configuration")
        conn_layout = QHBoxLayout(conn_group)
        conn_layout.setContentsMargins(20, 25, 20, 20)
        
        self.ip_input = QLineEdit("127.0.0.1")
        self.ip_input.setPlaceholderText("Enter server IP address...")
        self.ip_input.setFixedWidth(200)
        
        self.connect_btn = QPushButton("Connect Server")
        self.connect_btn.setObjectName("success_btn")
        self.connect_btn.clicked.connect(self.toggle_connection)
        
        self.status_label = QLabel("● Waiting for connection")
        self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold; margin-left: 10px;")
        
        conn_layout.addWidget(QLabel("Server Address:"))
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.status_label)
        conn_layout.addStretch()
        layout.addWidget(conn_group)
        
        # Navigation & Path Bar
        nav_panel = QFrame()
        nav_layout = QHBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        
        self.back_btn = QPushButton("⤴ Up One Level")
        self.back_btn.setFixedWidth(140)
        self.back_btn.clicked.connect(self.navigate_back)
        
        self.path_label = QLabel("/")
        self.path_label.setObjectName("path_text")
        
        self.mkdir_btn = QPushButton("+ New Folder")
        self.mkdir_btn.setObjectName("success_btn")
        self.mkdir_btn.setFixedWidth(120)
        self.mkdir_btn.clicked.connect(self.create_folder)
        
        nav_layout.addWidget(self.back_btn)
        nav_layout.addWidget(QLabel(" 📂 "))
        nav_layout.addWidget(self.path_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self.mkdir_btn)
        layout.addWidget(nav_panel)

        # Splitter for Files and Logs
        splitter = QSplitter(Qt.Horizontal)
        
        # File Area
        file_area = QWidget()
        file_layout = QVBoxLayout(file_area)
        file_layout.setContentsMargins(0, 0, 5, 0)
        
        action_bar = QHBoxLayout()
        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.clicked.connect(self.refresh_files)
        
        self.upload_btn = QPushButton("⬆ Upload File")
        self.upload_btn.clicked.connect(self.upload_file)
        
        self.download_btn = QPushButton("⬇ Download Seçted")
        self.download_btn.clicked.connect(self.download_file)
        
        self.qr_btn = QPushButton("📱 Share QR")
        self.qr_btn.clicked.connect(self.share_via_qr)
        
        self.delete_btn = QPushButton("🗑 Delete")
        self.delete_btn.setObjectName("danger_btn")
        self.delete_btn.clicked.connect(self.delete_file)
        
        action_bar.addWidget(self.refresh_btn)
        action_bar.addWidget(self.upload_btn)
        action_bar.addWidget(self.download_btn)
        action_bar.addWidget(self.qr_btn)
        action_bar.addWidget(self.delete_btn)
        file_layout.addLayout(action_bar)
        
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Item Name", "Size", "Type"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setColumnWidth(0, 300)
        self.file_tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        file_layout.addWidget(self.file_tree)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        file_layout.addWidget(self.progress_bar)
        
        splitter.addWidget(file_area)
        
        # Log Area
        log_area = QWidget()
        log_layout = QVBoxLayout(log_area)
        log_layout.setContentsMargins(5, 0, 0, 0)
        log_layout.addWidget(QLabel("System Logs"))
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        
        splitter.addWidget(log_area)
        splitter.setSizes([700, 300])
        
        layout.addWidget(splitter)
        self.enable_buttons(False)

    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)

    def update_time(self):
        self.time_label.setText(datetime.now().strftime("🕒 %Y-%m-%d %H:%M:%S"))

    def toggle_connection(self):
        if self.worker.connected:
            self.worker.do_disconnect()
        else:
            self.worker.server_ip = self.ip_input.text().strip()

    def update_connection_status(self, connected):
        if connected:
            self.status_label.setText("● Connected")
            self.status_label.setStyleSheet("color: #2ecc71; font-weight: bold; margin-left: 10px;")
            self.connect_btn.setText("Disconnect")
            self.connect_btn.setObjectName("danger_btn")
        else:
            self.status_label.setText("● Waiting for connection")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold; margin-left: 10px;")
            self.connect_btn.setText("Connect Server")
            self.connect_btn.setObjectName("success_btn")
        
        self.connect_btn.setStyle(self.connect_btn.style())
        self.enable_buttons(connected)

    def enable_buttons(self, enabled):
        for btn in [self.refresh_btn, self.upload_btn, self.download_btn, self.qr_btn, self.delete_btn, self.mkdir_btn, self.back_btn]:
            btn.setEnabled(enabled)

    def add_log_message(self, message, msg_type="info"):
        color = {"success": "#27ae60", "error": "#e74c3c", "warning": "#f39c12"}.get(msg_type, "#3498db")
        self.log_text.append(f'<span style="color: {color};">[{datetime.now().strftime("%H:%M:%S")}] {message}</span>')

    def update_file_tree(self, items, current_path):
        self.current_path = current_path
        self.path_label.setText(f"Location: /{current_path}")
        self.file_tree.clear()
        
        for item in items:
            name = item['name']
            is_dir = item['is_dir']
            size = item['size']
            
            size_str = ""
            if not is_dir:
                if size >= 1048576: size_str = f"{size/1048576:.1f} MB"
                elif size >= 1024: size_str = f"{size/1024:.1f} KB"
                else: size_str = f"{size} B"
            
            type_str = "📁 Folder" if is_dir else "📄 File"
            tree_item = QTreeWidgetItem([name, size_str, type_str])
            if is_dir: tree_item.setForeground(0, Qt.blue)
            self.file_tree.addTopLevelItem(tree_item)

    def on_item_double_clicked(self, item, column):
        if item.text(2) == "📁 Folder":
            new_path = os.path.join(self.current_path, item.text(0)).replace("\\", "/")
            self.worker.send_json({"command": "LIST", "path": new_path})

    def navigate_back(self):
        if self.current_path:
            parent = os.path.dirname(self.current_path.rstrip("/")).replace("\\", "/")
            if parent == ".": parent = ""
            self.worker.send_json({"command": "LIST", "path": parent})

    def create_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder Name:")
        if ok and name:
            self.worker.send_json({"command": "MKDIR", "dirname": name, "path": self.current_path})
            QTimer.singleShot(500, self.refresh_files)

    def refresh_files(self):
        self.worker.send_json({"command": "LIST", "path": self.current_path})

    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            threading.Thread(target=self.worker.upload_file, args=(path, self.current_path), daemon=True).start()

    def download_file(self):
        item = self.file_tree.currentItem()
        if item and item.text(2) != "📁 Folder":
            filename = item.text(0)
            save_path, _ = QFileDialog.getSaveFileName(self, "Save File", filename)
            if save_path:
                self.progress_bar.setValue(0)
                self.progress_bar.setVisible(True)
                threading.Thread(target=self.worker.download_file, args=(filename, self.current_path, save_path), daemon=True).start()

    def delete_file(self):
        item = self.file_tree.currentItem()
        if item:
            name = item.text(0)
            if QMessageBox.question(self, "Confirm Delete", f"Delete {name}?") == QMessageBox.Yes:
                self.worker.send_json({"command": "DELETE", "filename": name, "path": self.current_path})
                QTimer.singleShot(500, self.refresh_files)

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        if value >= 100: QTimer.singleShot(1000, lambda: self.progress_bar.setVisible(False))

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)

    def share_via_qr(self):
        item = self.file_tree.currentItem()
        if not item or item.text(2) == "📁 Folder":
            self.show_error("Please select a file to share via QR.")
            return
            
        filename = item.text(0)
        full_path = f"{self.current_path}/{filename}".strip("/")
        
        # URL encode path to handle spaces and special characters
        encoded_path = urllib.parse.quote(full_path)
        
        server_ip = self.ip_input.text().strip()
        
        # Security Check: If user uses 127.0.0.1, the phone won't be able to connect
        if server_ip == "127.0.0.1":
            try:
                # Reliable LAN IP detection
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except:
                local_ip = socket.gethostbyname(socket.gethostname())

            reply = QMessageBox.warning(self, "IP Connection Warning", 
                f"You are using '127.0.0.1'. Your phone cannot access this.\n\n"
                f"Your PC's local IP seems to be: {local_ip}\n"
                "Do you want to use this IP instead? (Highly recommended for QR Sharing)",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                server_ip = local_ip
                self.ip_input.setText(local_ip)
                # Reconnect automatically with new IP
                self.toggle_connection() 
        
        url = f"http://{server_ip}:9000/{encoded_path}"
        
        dialog = QRDialog(url, self)
        dialog.exec()

    def closeEvent(self, event):
        self.worker.running = False
        self.worker.do_disconnect()
        self.worker.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    client = PySideClient()
    client.show()
    sys.exit(app.exec())