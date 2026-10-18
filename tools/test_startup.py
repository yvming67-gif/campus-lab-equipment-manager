"""Real-socket, HTTP and archive checks for the Windows launcher; standard library only."""
import json,os,socket,subprocess,sys,tempfile,threading,unittest
from unittest.mock import patch
import windows_paths
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
    def test_english_windows_output_is_utf8(self):
        env=os.environ.copy();env.update({"PYTHONIOENCODING":"cp1252","PYTHONUTF8":"0"})
        result=subprocess.run([sys.executable,launcher.__file__,"--help"],capture_output=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("校园实验室",result.stdout.decode("utf-8"))
    @unittest.skipUnless(os.name=="nt","Windows directory junction validation")
    def test_chinese_temp_with_short_names_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/"中文项目";root.mkdir();chinese=base/"中文临时目录";chinese.mkdir()
            with patch("windows_paths._short_ascii",return_value=None),patch("windows_paths.tempfile.gettempdir",return_value=str(chinese)),patch.dict(os.environ,{"PUBLIC":str(base/"public")},clear=False):
                alias=windows_paths.ascii_alias(root)
                try:
                    self.assertTrue(str(alias).isascii());self.assertEqual(alias.resolve(),root.resolve())
                    self.assertEqual(windows_paths.ascii_alias(root),alias)
                finally:os.rmdir(alias)
    @unittest.skipUnless(os.name=="nt","Windows directory junction validation")
    def test_directory_alias_cannot_target_another_project(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/"中文项目";root.mkdir();other=base/"other";other.mkdir()
            aliases=base/"public/Documents/CampusLabRuntime";aliases.mkdir(parents=True)
            alias=aliases/windows_paths._alias_name(root.resolve())
            subprocess.run(["cmd","/d","/c","mklink","/J",str(alias),str(other)],check=True,capture_output=True)
            try:
                with patch("windows_paths._short_ascii",return_value=None),patch("windows_paths.tempfile.gettempdir",return_value=str(base/"中文临时目录")),patch.dict(os.environ,{"PUBLIC":str(base/"public")},clear=False):
                    with self.assertRaises(RuntimeError):windows_paths.ascii_alias(root)
            finally:os.rmdir(alias)
    def test_invalid_port_is_rejected(self):
        with self.assertRaises(launcher.StartupError):launcher.available_port(0)

if __name__=="__main__":unittest.main(verbosity=2)
