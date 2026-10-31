"""Run with:  python -m unittest test_cisco_config_tool -v   (no tkinter needed)"""
import json
import os
import socket
import struct
import tempfile
import threading
import unittest

import cisco_config_tool as c

SAMPLE_XML = b"\xef\xbb\xbf<d>001122AABBCC 001122aabbcc</d>\r\n"


class MacTests(unittest.TestCase):
    def test_formats(self):
        for raw in ("aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff", " AABBCCDDEEFF "):
            self.assertEqual(c.format_mac(raw), "AABBCCDDEEFF")

    def test_invalid(self):
        for raw in ("", "xyz", "AABBCCDDEEF", "AABBCCDDEEFFG0", "GG:BB:CC:DD:EE:FF"):
            with self.assertRaises(ValueError):
                c.format_mac(raw)

    def test_random(self):
        for _ in range(200):
            mac = c.random_mac()
            self.assertEqual(c.format_mac(mac), mac)
            first = int(mac[:2], 16)
            self.assertEqual(first & 0x01, 0)  # unicast
            self.assertEqual(first & 0x02, 2)  # locally administered


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for rel in ("8945/12.8", "8945/13.0", "8945/empty", "7945", "9951/v10", "9951/v9", "noSep"):
            os.makedirs(os.path.join(self.root, rel))
        for rel in ("8945/12.8/SEP001122AABBCC.cnf.xml", "8945/13.0/SEP001122AABBCC.cnf.xml",
                    "7945/SEP001122AABBCC.cnf", "9951/v10/SEP001122AABBCC.cnf.xml",
                    "9951/v9/sep001122aabbcc.cnf.xml"):
            with open(os.path.join(self.root, rel), "wb") as f:
                f.write(SAMPLE_XML)

    def tearDown(self):
        self.tmp.cleanup()

    def test_locations(self):
        self.assertEqual(c.find_locations(os.path.join(self.root, "8945")), ["12.8", "13.0"])
        self.assertEqual(c.find_locations(os.path.join(self.root, "7945")), [c.ROOT_LOCATION])
        self.assertEqual(c.find_locations(os.path.join(self.root, "9951")), ["v9", "v10"])  # natural sort
        self.assertEqual(c.find_locations(os.path.join(self.root, "noSep")), [])

    def test_structure_and_export(self):
        out = os.path.join(self.root, "out.json")
        c.export_structure(self.root, out)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(set(data), {"7945", "8945", "9951", "noSep"})
        self.assertEqual(list(data["8945"]), ["12.8", "13.0"])
        self.assertIn("(model folder)", data["7945"])
        self.assertEqual(data["noSep"], {})

    def test_generate(self):
        folder = os.path.join(self.root, "8945", "12.8")
        dest, count = c.generate_config(folder, "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF")
        self.assertTrue(dest.endswith("SEPAABBCCDDEEFF.cnf.xml"))
        self.assertEqual(count, 2)  # upper and lower case both replaced
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"\xef\xbb\xbf<d>AABBCCDDEEFF AABBCCDDEEFF</d>\r\n")  # BOM + CRLF kept
        self.assertFalse(os.path.exists(dest + ".tmp"))

    def test_generate_cnf_lowercase_sample_name(self):
        folder = os.path.join(self.root, "9951", "v9")
        dest, _ = c.generate_config(folder, "sep001122aabbcc.cnf.xml", "AABBCCDDEEFF")
        self.assertTrue(os.path.basename(dest).endswith("AABBCCDDEEFF.cnf.xml"))

    def test_overwrite_protection(self):
        folder = os.path.join(self.root, "8945", "12.8")
        c.generate_config(folder, "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF")
        with self.assertRaises(FileExistsError):
            c.generate_config(folder, "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF")
        c.generate_config(folder, "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF", overwrite=True)

    def test_same_mac_and_bad_name(self):
        folder = os.path.join(self.root, "8945", "12.8")
        with self.assertRaises(ValueError):
            c.generate_config(folder, "SEP001122AABBCC.cnf.xml", "001122AABBCC")
        with self.assertRaises(ValueError):
            c.generate_config(folder, "config.xml", "AABBCCDDEEFF")

    def test_bad_encoding_is_an_error_not_silent(self):
        folder = os.path.join(self.root, "7945")
        with open(os.path.join(folder, "SEP001122AABBCC.cnf"), "wb") as f:
            f.write(b"\xff\xfe\x00bad 001122AABBCC")
        with self.assertRaises(UnicodeDecodeError):
            c.generate_config(folder, "SEP001122AABBCC.cnf", "AABBCCDDEEFF")

    def test_check_path(self):
        self.assertTrue(c.check_path(self.root)[0])
        self.assertFalse(c.check_path(os.path.join(self.root, "nope"))[0])
        self.assertFalse(c.check_path("")[0])


class LogTests(unittest.TestCase):
    def test_log_and_tail(self):
        with tempfile.TemporaryDirectory() as d:
            log = c.ActivityLog(os.path.join(d, "a.log"))
            self.assertEqual(log.tail(), [])
            self.assertTrue(log.log("CREATE_CONFIG", "AABBCCDDEEFF", "line1\nline2"))
            log.log("CREATE_CONFIG", "112233445566", "boom", "FAILED")
            lines = log.tail()
            self.assertEqual(len(lines), 2)
            self.assertIn("SUCCESS", lines[0])
            self.assertIn("line1 line2", lines[0])  # newlines flattened
            self.assertIn("FAILED", lines[1])
            self.assertEqual(len(log.tail(1)), 1)

    def test_unwritable_log_does_not_raise(self):
        log = c.ActivityLog(os.path.join(tempfile.gettempdir(), "no_such_dir_xyz", "a.log"))
        self.assertFalse(log.log("X", "-", "y"))

    def test_do_generate_logs(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "SEP001122AABBCC.cnf.xml"), "wb") as f:
                f.write(SAMPLE_XML)
            log = c.ActivityLog(os.path.join(d, "a.log"))
            c.do_generate(log, d, "m/v", "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF")
            with self.assertRaises(ValueError):
                c.do_generate(log, d, "m/v", "SEP001122AABBCC.cnf.xml", "001122AABBCC")
            with self.assertRaises(FileExistsError):
                c.do_generate(log, d, "m/v", "SEP001122AABBCC.cnf.xml", "AABBCCDDEEFF")
            lines = log.tail()
            self.assertEqual(len(lines), 2)  # success + failed; FileExists not logged
            self.assertIn("FAILED", lines[1])


class TftpTests(unittest.TestCase):
    def _server(self, reply):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3)

        def run():
            try:
                data, addr = sock.recvfrom(1024)
                self.assertEqual(data[:2], b"\x00\x01")  # RRQ
                if reply is not None:
                    sock.sendto(reply, addr)
            except socket.timeout:
                pass

        t = threading.Thread(target=run)
        t.start()
        return sock, t

    def test_error_reply_means_alive(self):
        sock, t = self._server(struct.pack("!HH", 5, 1) + b"File not found\x00")
        ok, msg = c.check_tftp("127.0.0.1", sock.getsockname()[1], timeout=2)
        t.join(); sock.close()
        self.assertTrue(ok, msg)

    def test_garbage_reply(self):
        sock, t = self._server(b"hello")
        ok, _ = c.check_tftp("127.0.0.1", sock.getsockname()[1], timeout=2)
        t.join(); sock.close()
        self.assertFalse(ok)

    def test_timeout(self):
        sock, t = self._server(None)
        ok, msg = c.check_tftp("127.0.0.1", sock.getsockname()[1], timeout=0.5)
        t.join(); sock.close()
        self.assertFalse(ok)
        self.assertIn("timeout", msg)

    def test_empty_host(self):
        self.assertFalse(c.check_tftp("")[0])


if __name__ == "__main__":
    unittest.main()
