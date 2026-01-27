# 🚀 Mini Google Drive LAN (Sky Light Edition)

A professional, modern file-sharing application designed for local area networks. Built with Python and PySide6, featuring a clean "Sky Light" interface and instant mobile sharing.

## ✨ Features
- **🎨 Sky Light UI:** Clean, bright, and professional interface with high contrast for maximum readability.
- **📱 QR Code Sharing:** Instantly share files to mobile devices. Just scan the QR code to download via phone browser (Port 9000).
- **📂 Folder Support:** Full subdirectory navigation and creation.
- **📊 Real-time Progress:** Live progress bars for all uploads and downloads.
- **🔄 Persistent Connections:** Robust JSON-based protocol with automatic reconnection.
- **🚀 Single-Command Launch:** Use `main.py` to start both Server and Client instantly.

## 🎯 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Auto Launcher (Recommended)
```bash
python main.py
```
This will automatically launch the Server in the background and open the Client interface.

### 3. Manual Run
**Server:**
```bash
python pyside_server.py
```
**Client:**
```bash
python pyside_client.py
```

## 🖥️ Modern Interface

### Server
- ✅ **Sky Light Dashboard:** Clean aesthetic with white/soft gray palette.
- ✅ **HTTP Web Share:** Built-in mini web server for mobile downloads.
- ✅ **Request Monitoring:** Real-time logging of client and mobile activity.

### Client
- ✅ **Efficient File Tree:** Fast navigation with folder support.
- ✅ **QR Dialog:** Pop-up QR code generator for any file.
- ✅ **Responsive Design:** Interactive buttons and fluid layout.

## 📁 File Structure
```
├── pyside_server.py    # Modern LAN Server
├── pyside_client.py    # Modern UI Client
├── main.py             # One-click launcher
├── requirements.txt    # Project dependencies
├── storage/           # Server-side file repository
└── README.md          # Project documentation
```

## 🛠️ Requirements
- Python 3.8+
- PySide6
- qrcode[pil]
- Pillow

**Enjoy your Mini Google Drive LAN! 🌤️📁✨**