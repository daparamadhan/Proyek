import subprocess
import sys
import os
import time

def start_app():
    # Mengambil lokasi python yang sedang digunakan (termasuk jika dalam .venv)
    python_exe = sys.executable
    
    print("--- Mini Google Drive LAN ---")
    
    # Menjalankan Server di latar belakang
    print("[1/2] Menjalankan Server...")
    subprocess.Popen([python_exe, "pyside_server.py"], 
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    
    # Tunggu sebentar agar server siap
    time.sleep(1)
    
    # Menjalankan Client
    print("[2/2] Menjalankan Client...")
    try:
        subprocess.run([python_exe, "pyside_client.py"])
    except KeyboardInterrupt:
        print("\nAplikasi ditutup.")

if __name__ == "__main__":
    start_app()
