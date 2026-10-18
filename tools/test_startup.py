"""Real-socket, HTTP and archive checks for the Windows launcher; standard library only."""
import json,socket,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import start_local as launcher

class StartupTests(unittest.TestCase):
    def serve(self,payload):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):pass
        server=HTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server.server_port
    def test_busy_port_is_skipped(self):
        port=launcher.available_port(22000)
        with socket.socket() as busy:
            if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):busy.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            busy.bind(("127.0.0.1",port));busy.listen()
            free=launcher.available_port(port);self.assertNotEqual(free,port)
            with socket.socket() as probe:probe.bind(("127.0.0.1",free))
    def test_unrelated_site_not_reused(self):
        port=self.serve(json.dumps({"service":"campus-lab-demo","instance":"another-project"}).encode())
        self.assertFalse(launcher.instance_at(port,"this-project"))
    def test_matching_instance_is_reused(self):
        port=self.serve(json.dumps({"service":"campus-lab-demo","instance":"this-project"}).encode())
        self.assertTrue(launcher.instance_at(port,"this-project"))
    def test_malformed_readiness_is_rejected(self):
        self.assertFalse(launcher.instance_at(self.serve(b"<html>other app</html>"),"this-project"))
    def test_invalid_download_is_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"postgres.zip";file.write_bytes(b"invalid-download")
            with self.assertRaises(launcher.StartupError):launcher.download(file)
    def test_invalid_port_is_rejected(self):
        with self.assertRaises(launcher.StartupError):launcher.available_port(0)

if __name__=="__main__":unittest.main(verbosity=2)
