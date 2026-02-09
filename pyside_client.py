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
from PySide6.QtGui import QFont, QIcon, QPixmap, QImage, QPainter, QColor
import qrcode
import io
import shutil

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
        try:
            # Create socket without lock first to avoid holding it during connect
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3) # Short timeout to prevent long hangs
            sock.connect((self.server_ip, 5555))
            
            with self.socket_lock:
                self.sock = sock
                self.connected = True
                self.connection_changed.emit(True)
                self.log_message.emit(f"Connected to {self.server_ip}", "success")
                self.send_json_unlocked({"command": "LIST", "path": ""})
                
        except Exception as e:
            self.server_ip = "" # Reset IP to stop retry loop immediately
            self.error_occurred.emit(f"Connection failed: {e}")
            if self.sock:
                try: self.sock.close()
                except: pass

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
        
        self.apply_styles()
        self.setup_ui()
        self.setup_timer()
        
        # Start worker after UI is ready to avoid accessing missing widgets
        self.worker.start()

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #ffffff;
            }
            QWidget {
                color: #202124;
                font-family: 'Segoe UI', 'Roboto', Arial, sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #dadce0;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #5f6368;
            }
            QPushButton {
                background-color: #f1f3f4;
                color: #3c4043;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 500;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #e8eaed; /* Light gray hover */
                color: #202124;
            }
            QPushButton:pressed {
                background-color: #dadce0;
            }
            QPushButton:disabled {
                background-color: #f1f3f4;
                color: #bdc1c6;
            }
            
            /* Primary Action Button (Blue) */
            QPushButton#primary_btn {
                background-color: #1a73e8;
                color: white;
                font-weight: bold;
                border-radius: 20px; /* Rounded pill shape */
                padding: 10px 24px;
                font-size: 14px;
            }
            QPushButton#primary_btn:hover {
                background-color: #174ea6;
            }

            /* Danger Button (Red Text on hover or minimal) */
            QPushButton#danger_btn {
                background-color: white;
                color: #d93025;
                border: 1px solid #d93025;
            }
            QPushButton#danger_btn:hover {
                background-color: #fce8e6;
            }

            /* Success Button (Green) */
            QPushButton#success_btn {
                background-color: #1e8e3e;
                color: white;
            }
            QPushButton#success_btn:hover {
                background-color: #137333;
            }

            QLineEdit {
                background-color: #f1f3f4;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 10px;
                color: #202124;
                font-size: 14px;
            }
            QLineEdit:focus {
                background-color: #ffffff;
                border: 1px solid #1a73e8; /* Google Blue focus */
            }

            QTreeWidget {
                background-color: #ffffff;
                border: none;
                outline: none;
                padding: 5px;
            }
            QTreeWidget::item {
                padding: 8px 5px;
                border-bottom: 1px solid #f1f3f4;
                color: #3c4043;
            }
            QTreeWidget::item:selected {
                background-color: #e8f0fe; /* Light blue selection */
                color: #1967d2; /* Blue text */
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #ffffff;
                color: #5f6368;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #dadce0;
                font-weight: bold;
                font-size: 13px;
                text-transform: uppercase;
            }

            QTextEdit {
                background-color: #f8f9fa;
                border: 1px solid #dadce0;
                border-radius: 4px;
                color: #3c4043;
                font-family: 'Consolas', monospace;
                font-size: 12px;
            }
            
            QProgressBar {
                border: none;
                background-color: #e0e0e0;
                border-radius: 2px;
                height: 4px; /* Thin progress bar like loading indicators */
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #1a73e8;
                border-radius: 2px;
            }

            QLabel#header_title {
                color: #5f6368;
                font-size: 22px;
                font-family: 'Product Sans', 'Segoe UI', sans-serif; /* Try Product Sans if available */
                font-weight: normal; 
            }
            QLabel#logo_icon {
                font-size: 28px;
            }
            
            QLabel#path_text {
                color: #5f6368;
                font-size: 14px;
                padding: 4px 10px;
                background-color: transparent;
                border: 1px solid #dadce0;
                border-radius: 16px;
            }
            
            /* Custom Scrollbar for modern look */
            QScrollBar:vertical {
                border: none;
                background: #f1f3f4;
                width: 10px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #dadce0;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #bdc1c6;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            
            /* Splitter Handle */
            QSplitter::handle {
                background-color: #dadce0; 
                width: 1px;
            }
        """)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        # Main horizontal layout (Sidebar | Content)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === Sidebar ===
        sidebar = QFrame()
        # Significantly increased width to prevent any truncation
        sidebar.setStyleSheet("background-color: #ffffff; border-right: 1px solid #dadce0; min-width: 300px; max-width: 350px;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 20, 20, 20)
        sidebar_layout.setSpacing(10)

        # App Logo/Title
        title_box = QHBoxLayout()
        # Removed ruler icon as requested
        
        title_label = QLabel("Mini Drive LAN")
        title_label.setObjectName("header_title")
        # Reduced font to 18px and use standard font to ensure width is predictable
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #5f6368; font-family: 'Segoe UI', sans-serif; padding-left: 5px;")
        title_label.setWordWrap(False)
        
        # title_box.addWidget(logo_icon) # Removed
        title_box.addWidget(title_label)
        title_box.addStretch()
        sidebar_layout.addLayout(title_box)
        
        sidebar_layout.addSpacing(20)

        # "New" Button (Primary Call to Action)
        self.new_btn = QPushButton("+  New / Upload")
        self.new_btn.setObjectName("primary_btn") 
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.clicked.connect(self.upload_file) 
        
        new_btn_wrapper = QHBoxLayout()
        new_btn_wrapper.addWidget(self.new_btn)
        new_btn_wrapper.addStretch()
        sidebar_layout.addLayout(new_btn_wrapper)

        sidebar_layout.addSpacing(15)

        # Sidebar Navigation Items - Keeping only implemented ones
        nav_items = [
            ("My Drive", "🖿")
        ]
        
        for name, icon in nav_items:
            btn = QPushButton(f"  {icon}   {name}")
            if name == "My Drive":
                 btn.setStyleSheet("text-align: left; padding: 10px 15px; border-radius: 0 20px 20px 0; margin-right: 10px; background-color: #e8f0fe; color: #1967d2; font-weight: bold; border: none;")
            btn.setCursor(Qt.PointingHandCursor)
            sidebar_layout.addWidget(btn)

        # Storage usage indicator
        try:
            total, used, free = shutil.disk_usage("/")
            gb_used = used / (2**30)
            gb_total = total / (2**30)
            percent_used = (used / total) * 100
        except:
            gb_used = 0
            gb_total = 0
            percent_used = 0
        
        storage_label = QLabel("☁ Storage (Local Disk)")
        storage_label.setStyleSheet("color: #5f6368; font-size: 13px; margin-top: 10px;")
        sidebar_layout.addWidget(storage_label)
        
        storage_bar = QProgressBar()
        storage_bar.setValue(int(percent_used)) 
        storage_bar.setStyleSheet("""
            QProgressBar { min-height: 4px; max-height: 4px; background: #e0e0e0; border-radius: 2px; } 
            QProgressBar::chunk { background: #1a73e8; }
        """)
        sidebar_layout.addWidget(storage_bar)
        
        usage_text = QLabel(f"{gb_used:.1f} GB of {gb_total:.1f} GB used")
        usage_text.setStyleSheet("color: #5f6368; font-size: 12px;")
        sidebar_layout.addWidget(usage_text)

        main_layout.addWidget(sidebar)

        # === Main Content Area ===
        content_area = QWidget()
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(20, 15, 20, 10)
        content_layout.setSpacing(15)
        
        # Top Header (Search Bar Area)
        top_header = QHBoxLayout()
        
        # Search Bar
        search_container = QFrame()
        search_container.setStyleSheet("background-color: #f1f3f4; border-radius: 8px; min-width: 300px; max-width: 600px;")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(10, 5, 10, 5)
        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("color: #5f6368; font-size: 16px; border: none; background: transparent;")
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search in Drive")
        self.search_input.setStyleSheet("background: transparent; border: none; font-size: 16px;")
        self.search_input.textChanged.connect(self.filter_files) # Connect filter function
        
        search_layout.addWidget(search_icon)
        search_layout.addWidget(self.search_input)
        
        top_header.addWidget(search_container)
        top_header.addStretch()
        
        # User Avatar
        user_avatar = QLabel(self.username[0])
        user_avatar.setFixedSize(32, 32)
        user_avatar.setAlignment(Qt.AlignCenter)
        user_avatar.setStyleSheet("background-color: #7b1fa2; color: white; border-radius: 16px; font-weight: bold; font-size: 16px;")
        top_header.addWidget(user_avatar)
        
        content_layout.addLayout(top_header)
        
        # Connection Panel
        conn_group = QFrame()
        conn_group.setStyleSheet("background-color: #e8f0fe; border-radius: 8px; border: 1px solid #dcdde1;")
        conn_layout = QHBoxLayout(conn_group)
        conn_layout.setContentsMargins(15, 10, 15, 10)
        
        self.ip_input = QLineEdit("127.0.0.1")
        self.ip_input.setPlaceholderText("Enter server IP...")
        self.ip_input.setFixedWidth(150)
        self.ip_input.setStyleSheet("background: white; border: 1px solid #dadce0; border-radius: 4px; padding: 4px 8px;")
        
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setStyleSheet("background-color: #1a73e8; color: white; border-radius: 4px; padding: 6px 15px; font-weight: bold;")
        self.connect_btn.clicked.connect(self.toggle_connection)
        
        self.status_label = QLabel("● Waiting")
        self.status_label.setStyleSheet("color: #ea4335; font-weight: bold; margin-left: 10px; border: none; background: transparent;")
        
        conn_layout.addWidget(QLabel("🔌 Server Connection:"))
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.status_label)
        conn_layout.addStretch()
        
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color: #5f6368; font-size: 13px; border: none; background: transparent;")
        conn_layout.addWidget(self.time_label)
        
        content_layout.addWidget(conn_group)


        # Breadcrumb / Action Bar
        action_bar = QHBoxLayout()
        
        self.path_label = QLabel("My Drive")
        self.path_label.setObjectName("header_title") # Reuse style for big text
        self.path_label.setStyleSheet("font-size: 18px; color: #202124;")
        
        action_bar.addWidget(self.path_label)
        action_bar.addStretch()
        
        # List/Grid toggle buttons (placeholder visuals)
        list_view_btn = QPushButton("☰")
        grid_view_btn = QPushButton("▦")
        for btn in [list_view_btn, grid_view_btn]:
            btn.setFixedSize(30, 30)
            btn.setStyleSheet("border: none; background: transparent; color: #5f6368; font-size: 16px;")
            action_bar.addWidget(btn)
            
        content_layout.addLayout(action_bar)
        
        # Navigation Actions
        nav_actions = QHBoxLayout()
        self.back_btn = QPushButton("⤴ Up")
        self.back_btn.setStyleSheet("background-color: transparent; border: 1px solid #dadce0; color: #5f6368; border-radius: 16px; padding: 5px 15px;")
        self.back_btn.clicked.connect(self.navigate_back)
        
        self.mkdir_btn = QPushButton("📁 New Folder")
        self.mkdir_btn.setStyleSheet("background-color: transparent; border: 1px solid #dadce0; color: #5f6368; border-radius: 16px; padding: 5px 15px;")
        self.mkdir_btn.clicked.connect(self.create_folder)
        
        nav_actions.addWidget(self.back_btn)
        nav_actions.addWidget(self.mkdir_btn)
        
        # Explicit Upload Button for visibility
        self.upload_btn_nav = QPushButton("⬆ Upload")
        self.upload_btn_nav.setStyleSheet("background-color: transparent; border: 1px solid #dadce0; color: #5f6368; border-radius: 16px; padding: 5px 15px;")
        self.upload_btn_nav.clicked.connect(self.upload_file)
        nav_actions.addWidget(self.upload_btn_nav)
        
        nav_actions.addStretch()
        content_layout.addLayout(nav_actions)

        # File List Header Label
        file_header_label = QLabel("Files")
        file_header_label.setStyleSheet("font-weight: 500; font-size: 14px; color: #5f6368; margin-top: 10px; margin-bottom: 5px;")
        content_layout.addWidget(file_header_label)

        # File Content Area (Splitter with logs)
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(1)
        
        # File Tree
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Name", "Owner", "Last Modified", "File Size"])
        self.file_tree.setColumnWidth(0, 400) # Give name more space
        self.file_tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        # Hide standard header style to customize if needed, but keeping default
        splitter.addWidget(self.file_tree)
        
        # Progress Bar integrated above logs
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        content_layout.addWidget(self.progress_bar)

        # Bottom Action Bar (Contextual)
        bottom_actions = QHBoxLayout()
        
        # Right aligned actions
        bottom_actions.addStretch()
        
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_files)
        
        self.download_btn = QPushButton("⬇ Download")
        self.download_btn.clicked.connect(self.download_file)
        
        self.qr_btn = QPushButton("📱 Share via QR")
        self.qr_btn.clicked.connect(self.share_via_qr)
        
        self.delete_btn = QPushButton("🗑 Delete")
        self.delete_btn.setObjectName("danger_btn")
        self.delete_btn.clicked.connect(self.delete_file)
        
        for btn in [self.refresh_btn, self.download_btn, self.qr_btn, self.delete_btn]:
            btn.setStyleSheet("margin-left: 5px; padding: 6px 12px; border-radius: 4px; border: 1px solid #dadce0; background: white; color: #5f6368;")
            if btn == self.delete_btn:
                 btn.setStyleSheet("margin-left: 5px; padding: 6px 12px; border-radius: 4px; border: 1px solid #ed6c78; background: white; color: #d93025;")

            bottom_actions.addWidget(btn)
            
        content_layout.addLayout(bottom_actions)
        
        # Log Panel (Collapsible-ish)
        log_widget = QWidget()
        log_layout_inner = QVBoxLayout(log_widget)
        log_layout_inner.setContentsMargins(0, 5, 0, 0)
        log_header = QLabel("Activity Log")
        log_header.setStyleSheet("font-size: 12px; font-weight: bold; color: #5f6368; text-transform: uppercase;")
        log_layout_inner.addWidget(log_header)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout_inner.addWidget(self.log_text)
        
        splitter.addWidget(log_widget)
        
        content_layout.addWidget(splitter)
        
        main_layout.addWidget(content_area)
        
        self.enable_buttons(False)

        # Set stretch factor for sidebar vs content
        main_layout.setStretch(0, 0) # Sidebar auto
        main_layout.setStretch(1, 1) # Content expansive

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
        # Update to include new buttons and exclude removed ones
        buttons = [self.refresh_btn, self.new_btn, self.download_btn, self.qr_btn, self.delete_btn, self.mkdir_btn, self.back_btn]
        for btn in buttons:
            btn.setEnabled(enabled)

    def add_log_message(self, message, msg_type="info"):
        color = {"success": "#1e8e3e", "error": "#d93025", "warning": "#f9ab00"}.get(msg_type, "#1a73e8")
        self.log_text.append(f'<span style="color: {color};">[{datetime.now().strftime("%H:%M")}] {message}</span>')

    def update_file_tree(self, items, current_path):
        self.current_path = current_path
        # Update breadcrumb
        if current_path:
             parts = current_path.split("/")
             display_path = " > ".join(parts)
             self.path_label.setText(f"My Drive > {display_path}")
        else:
             self.path_label.setText("My Drive")
             
        self.file_tree.clear()
        
        for item in items:
            name = item['name']
            is_dir = item['is_dir']
            size = item['size']
            mtime = item.get('mtime', 0)
            
            # Format Size
            size_str = ""
            if not is_dir:
                if size >= 1073741824: size_str = f"{size/1073741824:.1f} GB"
                elif size >= 1048576: size_str = f"{size/1048576:.1f} MB"
                elif size >= 1024: size_str = f"{size/1024:.1f} KB"
                else: size_str = f"{size} B"
            else:
                size_str = "-"
            
            # Format Date
            date_str = datetime.fromtimestamp(mtime).strftime("%b %d, %Y") if mtime else "-"
            
            # Owner (Mock)
            owner = "me"
            
            # Columns: Name, Owner, Last Modified, File Size
            # item.text(0) is Name, item.text(3) is Size for sorting if needed, but we used text.
            # We need to store is_dir info somewhere, usually UserRole or just keep track.
            # TreeItem construct: [Name, Owner, Last Modified, Size]
            
            tree_item = QTreeWidgetItem([name, owner, date_str, size_str])
            
            # Set Icon
            if is_dir:
                tree_item.setIcon(0, QIcon(self.create_icon("📁", "#5f6368")))
                tree_item.setForeground(0, Qt.black) # Keep text standard color
            else:
                # Simple file icon logic
                if name.endswith(('.png', '.jpg', '.jpeg')):
                    icon_char = "🖼"
                elif name.endswith(('.pdf', '.doc', '.txt')):
                    icon_char = "📄"
                elif name.endswith(('.mp4', '.avi')):
                    icon_char = "🎬"
                elif name.endswith(('.mp3', '.wav')):
                    icon_char = "🎵"
                else:
                    icon_char = "📄"
                tree_item.setIcon(0, QIcon(self.create_icon(icon_char, "#1a73e8")))

            # Store metadata for logic usage (is_dir)
            tree_item.setData(0, Qt.UserRole, is_dir)
            
            self.file_tree.addTopLevelItem(tree_item)
            
    def create_icon(self, text, color):
        # Helper to create QIcon from text (emoji)
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setFont(QFont("Segoe UI Emoji", 24))
        painter.setPen(QColor(color))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
        painter.end()
        return pixmap

    def filter_files(self, text):
        search_text = text.lower()
        root = self.file_tree.invisibleRootItem()
        child_count = root.childCount()
        
        for i in range(child_count):
            item = root.child(i)
            name = item.text(0).lower()
            if search_text in name:
                item.setHidden(False)
            else:
                item.setHidden(True)

    def on_item_double_clicked(self, item, column):
        is_dir = item.data(0, Qt.UserRole)
        if is_dir:
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
        if item and not item.data(0, Qt.UserRole):
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
        if not item or item.data(0, Qt.UserRole):
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