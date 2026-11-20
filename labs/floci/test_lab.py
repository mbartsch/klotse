"""Offline tests for lab-only Docker discovery and credential handling."""

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import identity
import start


class BootstrapTest(unittest.TestCase):
    def test_accepts_owned_loopback_api(self):
        api = {"server": "https://127.0.0.1:6443", "certificate-authority-data": "dummy"}
        data = {"clusters": [{"cluster": api}], "users": [{"user": {}}]}
        container = {"NetworkSettings": {"Ports": {"6443/tcp": [
            {"HostIp": "127.0.0.1", "HostPort": "16444"},
        ]}}}
        with patch.object(identity, "docker", side_effect=[
            "id", json.dumps([container]), json.dumps(data),
        ]) as docker:
            _, cluster = identity.bootstrap("eks", "klotse-eks-1")
        self.assertEqual(cluster["server"], "https://127.0.0.1:16444")
        self.assertIn("label=floci_namespace=klotse793", docker.call_args_list[0].args)
        self.assertIn("label=io.floci.resource-id=klotse-eks-1", docker.call_args_list[0].args)

    def test_wildcard_binding_warns_and_still_uses_loopback(self):
        for ip in ("0.0.0.0", "::"):
            with self.subTest(ip=ip):
                api = {"server": "https://0.0.0.0:6443", "certificate-authority-data": "dummy"}
                data = {"clusters": [{"cluster": api}], "users": [{"user": {}}]}
                container = {"NetworkSettings": {"Ports": {"6443/tcp": [{"HostIp": ip, "HostPort": "16444"}]}}}
                with patch.object(identity, "docker", side_effect=["id", json.dumps([container]), json.dumps(data)]):
                    with patch("sys.stderr", new_callable=io.StringIO) as err:
                        _, cluster = identity.bootstrap("eks", "klotse-eks-1")
                self.assertEqual(cluster["server"], "https://127.0.0.1:16444")
                self.assertIn("WARNING", err.getvalue())

    def test_rejects_missing_or_wrong_port_before_reading_credentials(self):
        for ip, port in (("127.0.0.1", "6443"), ("0.0.0.0", "6443")):
            with self.subTest(ip=ip, port=port):
                container = {"NetworkSettings": {"Ports": {"6443/tcp": [
                    {"HostIp": ip, "HostPort": port},
                ]}}}
                with patch.object(identity, "docker", side_effect=[
                    "id", json.dumps([container]),
                ]) as docker:
                    with self.assertRaises(SystemExit):
                        identity.bootstrap("eks", "klotse-eks-1")
                self.assertEqual(docker.call_count, 2)

    def test_existing_config_is_not_overwritten(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "aws.kubeconfig"
            identity.write_config(target, {"value": "first"})
            with self.assertRaises(FileExistsError):
                identity.write_config(target, {"value": "second"})
            self.assertEqual(json.loads(target.read_text()), {"value": "first"})


class StartTest(unittest.TestCase):
    def test_warns_about_unsafe_default_bridge_and_starts(self):
        with patch.object(start, "engine_host", return_value="unix:///tmp/lab.sock"), patch.object(start.subprocess, "run") as run:
            run.return_value.stdout = "0.0.0.0\n"
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                start.main()
        self.assertIn("Docker's default bridge publishes ports on 0.0.0.0", err.getvalue())
        self.assertEqual(run.call_args.args[0][:2], ["docker", "compose"])

    def test_warns_about_unsafe_user_defined_bridge_and_removes_probe(self):
        with patch.object(start, "engine_host", return_value="unix:///tmp/lab.sock"):
            with patch.object(start, "bridge_bind_ip", side_effect=["127.0.0.1", ""]):
                with patch.object(start.subprocess, "run") as run:
                    with patch("sys.stderr", new_callable=io.StringIO) as err:
                        start.main()
        create, remove, compose = [call.args[0] for call in run.call_args_list]
        self.assertEqual(create[:5], ["docker", "network", "create", "--driver", "bridge"])
        self.assertEqual(remove, ["docker", "network", "rm", create[-1]])
        self.assertEqual(compose[:2], ["docker", "compose"])
        self.assertIn("new Docker bridge network", err.getvalue())
        self.assertIn("all interfaces", err.getvalue())

    def test_loopback_defaults_give_no_warning(self):
        with patch.object(start, "engine_host", return_value="unix:///tmp/lab.sock"), patch.object(start.subprocess, "run") as run:
            run.return_value.stdout = "127.0.0.1\n"
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                start.main()
        self.assertEqual(err.getvalue(), "")

    def test_passes_selected_engine_socket_to_compose(self):
        with patch.object(start, "engine_host", return_value="unix:///tmp/lab.sock"):
            with patch.object(start.subprocess, "run") as run:
                run.return_value.stdout = "127.0.0.1\n"
                start.main()
        self.assertEqual(run.call_args.kwargs["env"]["KLOTSE_LAB_DOCKER_SOCKET"], "/tmp/lab.sock")

    def test_refuses_remote_engine_before_inspecting_bridge(self):
        for host in ("ssh://lab.example.invalid", "tcp://127.0.0.1:2375"):
            with self.subTest(host=host), patch.dict(os.environ, {"DOCKER_HOST": host}, clear=True):
                with patch.object(start.subprocess, "run") as run:
                    with self.assertRaises(SystemExit):
                        start.main()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
