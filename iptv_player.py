import os
import sys

# Compatibilidad de entorno en Ubuntu / Linux Wayland
os.environ["QT_QPA_PLATFORM"] = "xcb"

import re
import json
import shutil
import subprocess
import requests
from PyQt5 import QtWidgets, QtCore

CACHE_FILE = os.path.expanduser("~/.iptv_player_cache.json")

class M3UParser:
    @staticmethod
    def parse(content):
        channels = []
        current_name = "Canal"
        current_group = "General"
        
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            
            if line.startswith("#EXTINF:"):
                group_match = re.search(r'group-title="([^"]+)"', line, re.IGNORECASE)
                current_group = group_match.group(1).strip() if group_match else "General"
                
                name_match = re.search(r",([^,]+)$", line)
                current_name = name_match.group(1).strip() if name_match else "Canal"
            elif not line.startswith("#") and (line.startswith("http://") or line.startswith("https://")):
                channels.append({
                    "name": current_name,
                    "group": current_group if current_group else "General",
                    "url": line
                })
                current_name = "Canal"
                current_group = "General"
                
        return channels

class DownloadThread(QtCore.QThread):
    finished = QtCore.pyqtSignal(list, str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, source):
        super().__init__()
        self.source = source

    def run(self):
        try:
            if self.source.startswith("http://") or self.source.startswith("https://"):
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                response = requests.get(self.source, headers=headers, timeout=30)
                response.raise_for_status()
                content = response.text
            else:
                with open(self.source, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

            channels = M3UParser.parse(content)
            self.finished.emit(channels, self.source)
        except Exception as e:
            self.error.emit(str(e))

class IPTVPlayer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IPTV Player")
        self.setGeometry(150, 150, 500, 700)

        self.channels = []
        self.current_process = None

        self.init_ui()
        self.load_cache()

    def init_ui(self):
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setPlaceholderText("Introduce la URL de la lista...")
        layout.addWidget(self.url_input)

        self.btn_load = QtWidgets.QPushButton("Cargar / Actualizar Lista")
        self.btn_load.clicked.connect(self.load_playlist)
        layout.addWidget(self.btn_load)

        self.status_label = QtWidgets.QLabel("Listo")
        self.status_label.setStyleSheet("color: #2980b9; font-weight: bold;")
        layout.addWidget(self.status_label)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Buscar canal...")
        self.search_input.textChanged.connect(self.filter_channels)
        layout.addWidget(self.search_input)

        self.channel_tree = QtWidgets.QTreeWidget()
        self.channel_tree.setHeaderLabel("Canales y Categorías")
        self.channel_tree.itemClicked.connect(self.on_item_clicked)
        layout.addWidget(self.channel_tree)

        btn_stop = QtWidgets.QPushButton("Cerrar Reproductor de Vídeo")
        btn_stop.clicked.connect(self.stop_player)
        layout.addWidget(btn_stop)

    def populate_tree(self, channels_to_show):
        self.channel_tree.clear()
        groups = {}

        for ch in channels_to_show:
            grp = ch["group"]
            if grp not in groups:
                groups[grp] = []
            groups[grp].append(ch)

        for group_name in sorted(groups.keys()):
            group_item = QtWidgets.QTreeWidgetItem(self.channel_tree, [group_name])
            for ch in groups[group_name]:
                item = QtWidgets.QTreeWidgetItem(group_item, [ch["name"]])
                item.setData(0, QtCore.Qt.UserRole, ch["url"])

    def load_playlist(self):
        source = self.url_input.text().strip()
        if not source:
            return

        if not source.startswith("http://") and not source.startswith("https://") and not source.startswith("/"):
            source = "http://" + source
            self.url_input.setText(source)

        self.btn_load.setEnabled(False)
        self.status_label.setText("Descargando lista...")

        self.worker = DownloadThread(source)
        self.worker.finished.connect(self.on_download_finished)
        self.worker.error.connect(self.on_download_error)
        self.worker.start()

    def on_download_finished(self, channels, source):
        self.channels = channels
        self.populate_tree(self.channels)
        self.save_cache(source, self.channels)
        self.status_label.setText(f"{len(self.channels)} canales cargados.")
        self.btn_load.setEnabled(True)

    def on_download_error(self, err_msg):
        self.status_label.setText("Error al cargar.")
        self.btn_load.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, "Error", f"Fallo al descargar:\n{err_msg}")

    def filter_channels(self, query):
        query = query.strip().lower()
        if not query:
            self.populate_tree(self.channels)
            return

        filtered = [ch for ch in self.channels if query in ch["name"].lower() or query in ch["group"].lower()]
        self.populate_tree(filtered)
        self.channel_tree.expandAll()

    def on_item_clicked(self, item, column):
        url = item.data(0, QtCore.Qt.UserRole)
        
        if not url:
            item.setExpanded(not item.isExpanded())
            return

        if not shutil.which("mpv"):
            self.status_label.setText("Falta 'mpv' en el sistema.")
            QtWidgets.QMessageBox.critical(
                self,
                "Dependencia faltante",
                "El reproductor 'mpv' no está instalado.\nInstálalo con: sudo apt install -y mpv"
            )
            return

        self.stop_player()
        self.status_label.setText(f"Conectando: {item.text(0)}...")

        cmd = [
            "mpv",
            url,
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "--stream-lavf-o-append=reconnect=1",
            "--stream-lavf-o-append=reconnect_streamed=1",
            "--stream-lavf-o-append=reconnect_delay_max=2",
            "--demuxer-lavf-o-append=live_start_index=-1",
            "--demuxer-readahead-secs=5",
            "--cache=yes",
            "--demuxer-max-bytes=20M",
            "--framedrop=vo",
            "--title=" + item.text(0)
        ]

        try:
            self.current_process = subprocess.Popen(cmd)
            self.status_label.setText(f"En directo: {item.text(0)}")
        except Exception as e:
            self.status_label.setText("Error al ejecutar stream.")
            QtWidgets.QMessageBox.warning(self, "Error", f"No se pudo iniciar el vídeo: {str(e)}")

    def stop_player(self):
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
            self.current_process = None
            self.status_label.setText("Reproducción detenida.")

    def save_cache(self, source, channels):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"source": source, "channels": channels}, f, ensure_ascii=False)
        except Exception:
            pass

    def load_cache(self):
        if not os.path.exists(CACHE_FILE):
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.url_input.setText(data.get("source", ""))
                self.channels = data.get("channels", [])
                self.populate_tree(self.channels)
                self.status_label.setText(f"{len(self.channels)} canales listos en memoria.")
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_player()
        event.accept()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    player = IPTVPlayer()
    player.show()
    sys.exit(app.exec_())