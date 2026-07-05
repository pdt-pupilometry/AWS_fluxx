#!/usr/bin/env python3
"""
Servidor HTTP local + tunel ngrok para probar el endpoint de notificaciones
del pipeline sin necesitar un backend propio. Ver testing/TEST_ENDPOINT.md
para el detalle de uso.
"""

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent / "test_downloads"


class NotificationHandler(BaseHTTPRequestHandler):
    download_dir: Path = DEFAULT_DOWNLOAD_DIR

    def log_message(self, format, *args):
        pass  # silenciamos el log default; imprimimos nosotros el detalle

    def do_GET(self):
        body = b"Esperando notificaciones del pipeline (POST). Ver testing/TEST_ENDPOINT.md.\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            payload = {}

        print("\n" + "=" * 70)
        print(f"[{datetime.now().isoformat(timespec='seconds')}] POST {self.path}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        self._respond_ok()

        download_url = payload.get("download_url")
        if download_url:
            self._download(payload)
        else:
            print("Sin 'download_url' en el payload -- nada para descargar.")
        print("=" * 70 + "\n")

    def _respond_ok(self):
        body = b'{"status": "received"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, payload: dict):
        download_url = payload["download_url"]
        session_id = payload.get("session_id", "unknown")
        eye = payload.get("eye", "unknown")
        fmt = payload.get("format", "json")
        compressed = payload.get("compressed", False)

        print(f"Descargando: {download_url}")
        try:
            with urllib.request.urlopen(download_url, timeout=30) as resp:
                data = resp.read()
        except urllib.error.URLError as exc:
            print(f"ERROR descargando el archivo: {exc}")
            return

        if compressed:
            try:
                data = gzip.decompress(data)
            except OSError as exc:
                print(f"ERROR descomprimiendo gzip: {exc}")
                return

        self.download_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.download_dir / f"{session_id}_{eye}_{timestamp}.{fmt}"
        out_path.write_bytes(data)

        print(f"Guardado en: {out_path} ({len(data)} bytes)")
        if fmt == "json":
            try:
                records = json.loads(data)
                print(f"Registros: {len(records)}")
                if records:
                    print("Primer registro:", json.dumps(records[0], ensure_ascii=False))
            except json.JSONDecodeError:
                pass
        else:
            line_count = data.count(b"\n")
            print(f"Lineas (aprox filas + encabezado): {line_count}")


def start_ngrok(port: int) -> subprocess.Popen:
    if shutil.which("ngrok") is None:
        sys.exit(
            "ERROR: no se encontro el binario 'ngrok' en el PATH.\n"
            "Instalar con: brew install ngrok\n"
            "Y autenticar una vez con: ngrok config add-authtoken <tu-token>\n"
            "(token gratis en https://dashboard.ngrok.com)"
        )
    return subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_ngrok_url(retries: int = 40, delay: float = 0.5) -> str:
    for _ in range(retries):
        try:
            with urllib.request.urlopen(NGROK_API_URL, timeout=2) as resp:
                data = json.loads(resp.read())
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(delay)
    raise RuntimeError(
        "ngrok no expuso ningun tunel https a tiempo. "
        "Revisa que 'ngrok config add-authtoken <token>' ya se haya corrido una vez."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787, help="Puerto local del servidor (default: 8787)")
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=DEFAULT_DOWNLOAD_DIR,
        help=f"Carpeta donde se guardan los archivos descargados (default: {DEFAULT_DOWNLOAD_DIR})",
    )
    args = parser.parse_args()

    NotificationHandler.download_dir = args.download_dir

    try:
        server = ThreadingHTTPServer(("0.0.0.0", args.port), NotificationHandler)
    except OSError as exc:
        sys.exit(f"ERROR: no se pudo abrir el puerto {args.port}: {exc}")

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Servidor local escuchando en http://127.0.0.1:{args.port}")

    ngrok_proc = start_ngrok(args.port)
    try:
        public_url = wait_for_ngrok_url()
    except RuntimeError as exc:
        ngrok_proc.terminate()
        server.shutdown()
        sys.exit(f"ERROR: {exc}")

    print("=" * 70)
    print(f"Tunel ngrok listo:  {public_url}")
    print()
    print("Actualiza ENDPOINT_URL en tu .env con esta URL y despliega de nuevo:")
    print(f"  ENDPOINT_URL={public_url}")
    print("  ./scripts/deploy.sh")
    print()
    print("(Esto solo actualiza un parametro de la Lambda notifier, no toca")
    print(" los buckets S3 -- es un update rapido, sin rebuild de imagenes.)")
    print("=" * 70)
    print("\nEsperando notificaciones... (Ctrl+C para salir)\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        server.shutdown()
        ngrok_proc.terminate()
        try:
            ngrok_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ngrok_proc.kill()


if __name__ == "__main__":
    main()
