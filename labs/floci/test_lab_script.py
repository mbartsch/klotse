"""Offline tests for lab.py: no Docker, Terraform, openssl or network is used."""

import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, call, patch

import lab


class Answer(io.BytesIO):
    status = 200


class EnvTest(unittest.TestCase):
    def test_environment_points_at_the_emulators_and_private_files(self):
        with TemporaryDirectory() as tmp:
            env = lab.environment(Path(tmp))
            self.assertNotIn("SSL_CERT_FILE", env)  # no bundle written yet
            Path(tmp, "roots.pem").write_text("pem")
            env = lab.environment(Path(tmp))
        self.assertEqual(env["SSL_CERT_FILE"], str(Path(tmp, "roots.pem")))
        self.assertEqual(env["AWS_ENDPOINT_URL"], "http://127.0.0.1:4566")
        self.assertEqual(env["TF_VAR_CLIENT_HOST_OVERRIDES"], lab.OCI_OVERRIDE)
        self.assertEqual(env["CLOUDSDK_CONFIG"], str(Path(tmp, "gcloud")))
        self.assertTrue(env["CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER"].startswith("http://localhost:4588/"))
        for name, value in env.items():
            if name.endswith(("_URL", "_ENDPOINT", "_CONTAINER", "_SERVICEUSAGE")):
                self.assertRegex(value, r"^http://(127\.0\.0\.1|localhost):", name)

    def test_render_quotes_for_sh_and_powershell(self):
        env = {"A": "it's here", "B": "plain"}
        self.assertEqual(lab.render(env, "sh"), "export A='it'\"'\"'s here'\nexport B=plain\n")
        self.assertEqual(lab.render(env, "powershell"), "$env:A = 'it''s here'\n$env:B = 'plain'\n")

    def test_env_command_prints_exports(self):
        with patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
            lab.main(["env"])
        self.assertIn("export AWS_ENDPOINT_URL=http://127.0.0.1:4566\n", out.getvalue())

    def test_lab_env_drops_proxies_and_tracing(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy", "no_proxy": "x", "TF_LOG": "TRACE", "KEEP": "1"}, clear=True):
            env = lab.lab_env()
        self.assertEqual({"HTTPS_PROXY", "no_proxy", "TF_LOG"} & set(env), set())
        self.assertEqual(env["KEEP"], "1")


class ProvidersTest(unittest.TestCase):
    def test_selected_keeps_lab_order_and_refuses_unknown(self):
        self.assertEqual(lab.selected(None), ["aws", "azure", "gcp", "oci"])
        self.assertEqual(lab.selected("oci, aws"), ["aws", "oci"])
        with self.assertRaises(SystemExit):
            lab.selected("aws,ibm")

    def test_wait_healthy_retries_until_up_and_times_out(self):
        tries = {"aws": 0}

        def opener(url, timeout):
            tries["aws"] += 1
            if tries["aws"] < 3:
                raise OSError("refused")
            return Answer(b"ok")

        sleep = MagicMock()
        with patch("sys.stderr", new_callable=io.StringIO):
            lab.wait_healthy(["aws"], opener=opener, sleep=sleep)
        self.assertEqual(sleep.call_count, 2)
        clock = iter([0, 1000, 1000])
        with patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit) as stop:
            lab.wait_healthy(["gcp"], timeout=10, opener=MagicMock(side_effect=OSError("refused")), sleep=MagicMock(),
                             clock=lambda: next(clock))
        self.assertIn("logs gcp", str(stop.exception))
        self.assertIn("gcp: GET http://127.0.0.1:4588/_floci-gcp/health -> OSError: refused", err.getvalue())

    def test_gcp_uses_its_own_health_path(self):
        asked = []

        def opener(url, timeout):
            asked.append(url)
            if url == "http://127.0.0.1:4588/_floci/health":  # what floci-gcp 0.13.0 does not serve
                raise lab.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return Answer(b"ok")

        with patch("sys.stderr", new_callable=io.StringIO):
            lab.wait_healthy(["aws", "gcp"], opener=opener, sleep=MagicMock(side_effect=AssertionError("no retry")))
        self.assertIn("http://127.0.0.1:4588/_floci-gcp/health", asked)
        self.assertNotIn("http://127.0.0.1:4588/_floci/health", asked)

    def test_verbose_logs_each_attempt_and_falls_back_to_the_alias(self):
        def opener(url, timeout):
            if "_floci-gcp" in url:
                raise lab.urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
            return Answer(b"ok")

        with patch.object(lab, "VERBOSE", True), patch("sys.stderr", new_callable=io.StringIO) as err:
            lab.wait_healthy(["gcp"], opener=opener, sleep=MagicMock())
        text = err.getvalue()
        self.assertIn("[lab:debug] health gcp: GET http://127.0.0.1:4588/_floci-gcp/health -> HTTP 503", text)
        self.assertIn("[lab] gcp emulator is up (http://127.0.0.1:4588/health)", text)

    def test_quiet_by_default(self):
        with patch.object(lab, "VERBOSE", False), patch("sys.stderr", new_callable=io.StringIO) as err:
            lab.debug("hidden")
        self.assertEqual(err.getvalue(), "")

    def test_verbose_flag_before_or_after_the_command(self):
        for argv in (["-v", "env"], ["env", "--debug"]):
            with self.subTest(argv=argv), patch("sys.stdout", new_callable=io.StringIO), \
                    patch("sys.stderr", new_callable=io.StringIO) as err:
                lab.main(argv)
            self.assertIn("[lab:debug]", err.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO) as err:
            lab.main(["env"])
        self.assertNotIn("[lab:debug]", err.getvalue())

    def test_tf_log_is_opt_in(self):
        with patch.dict("os.environ", {"TF_LOG": "TRACE"}, clear=True), patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertNotIn("TF_LOG", lab.lab_env())
            self.assertEqual(lab.lab_env("DEBUG")["TF_LOG"], "DEBUG")
        self.assertIn("fixture credentials", err.getvalue())


class PrepareTest(unittest.TestCase):
    def test_azure_ca_is_appended_to_the_system_bundle(self):
        with TemporaryDirectory() as tmp:
            system = Path(tmp, "system.pem")
            system.write_bytes(b"SYSTEM\n")
            with patch.object(lab, "system_bundle", return_value=system):
                lab.prepare(["azure"], Path(tmp, "local"), fetch=lambda url, timeout: Answer(b"FLOCI\n"))
            self.assertEqual(Path(tmp, "local", "roots.pem").read_bytes(), b"SYSTEM\nFLOCI\n")
            self.assertEqual(Path(tmp, "local", "gcp-token").read_text().strip(), lab.GCP_TOKEN)

    def test_oci_profile_uses_the_generated_key_fingerprint(self):
        def run(argv, **kwargs):
            if argv[:2] == ["openssl", "genrsa"]:
                Path(argv[3]).write_text("KEY")
            return subprocess.CompletedProcess(argv, 0, stdout=b"\x01\x02")

        with TemporaryDirectory() as tmp, patch.object(lab.subprocess, "run", side_effect=run):
            local = Path(tmp)
            lab.prepare(["oci"], local)
            profile = (local / "oci-config").read_text()
        self.assertIn("fingerprint=" + ":".join(f"{b:02x}" for b in __import__("hashlib").md5(b"\x01\x02").digest()), profile)
        self.assertIn("key_file=" + (local / "oci.pem").as_posix(), profile)


class UpDownTest(unittest.TestCase):
    def run_up(self, argv, fail=None):
        calls = []

        def terraform(provider, *args, env, capture=False):
            calls.append((provider, args[0]))
            if (provider, args[0]) == fail:
                raise subprocess.CalledProcessError(1, "terraform")

        with patch.object(lab.start, "main") as start, patch.object(lab, "wait_healthy"), patch.object(lab, "prepare"), \
                patch.object(lab, "terraform", side_effect=terraform), patch("sys.stderr", new_callable=io.StringIO) as err:
            try:
                lab.main(argv)
            finally:
                start.assert_called_once()
        return calls, err.getvalue()

    def test_up_applies_each_root_with_a_saved_plan(self):
        calls, _ = self.run_up(["up", "--providers", "gcp,aws"])
        self.assertEqual(calls, [("aws", "init"), ("aws", "plan"), ("aws", "apply"),
                                 ("gcp", "init"), ("gcp", "plan"), ("gcp", "apply")])

    def test_up_stops_at_a_failure_and_explains_the_aws_conflict(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as stop:
                self.run_up(["up"], fail=("aws", "apply"))
        self.assertIn("terraform failed for aws", str(stop.exception))

    def test_aws_failure_mentions_the_409_recovery(self):
        calls = []

        def terraform(provider, *args, env, capture=False):
            calls.append(provider)
            raise subprocess.CalledProcessError(1, "terraform")

        with patch.object(lab.start, "main"), patch.object(lab, "wait_healthy"), patch.object(lab, "prepare"), \
                patch.object(lab, "terraform", side_effect=terraform), patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit):
                lab.main(["up"])
        self.assertEqual(calls, ["aws"])  # nothing after the failing root
        self.assertIn("409 Cluster already exists", err.getvalue())

    def test_down_destroys_in_reverse_then_stops_emulators(self):
        with TemporaryDirectory() as tmp:
            here = Path(tmp)
            for provider in lab.ORDER:
                (here / provider / ".terraform").mkdir(parents=True)
            with patch.object(lab, "HERE", here), patch.object(lab, "LOCAL", here / ".local"), \
                    patch.object(lab, "terraform") as terraform, patch.object(lab.subprocess, "run") as run, \
                    patch("sys.stderr", new_callable=io.StringIO):
                lab.main(["down"])
        self.assertEqual([c.args[0] for c in terraform.call_args_list], ["oci", "gcp", "azure", "aws"])
        self.assertEqual(run.call_args.args[0], ["docker", "compose", "-f", str(here / "compose.yaml"), "down", "--volumes"])

    def test_down_of_some_providers_keeps_emulators_and_removes_identities(self):
        with TemporaryDirectory() as tmp:
            here = Path(tmp)
            (here / "gcp" / ".terraform").mkdir(parents=True)
            (here / ".local").mkdir()
            (here / ".local" / "gcp.kubeconfig").write_text("{}")
            with patch.object(lab, "HERE", here), patch.object(lab, "LOCAL", here / ".local"), \
                    patch.object(lab, "terraform") as terraform, patch.object(lab.subprocess, "run") as run, \
                    patch("sys.stderr", new_callable=io.StringIO):
                lab.main(["down", "--providers", "gcp"])
        self.assertEqual(run.call_args_list, [call([lab.sys.executable, str(here / "identity.py"), "remove", "gcp"],
                                                   check=True, env=unittest.mock.ANY)])
        terraform.assert_called_once()

    def test_failed_destroy_leaves_emulators_running(self):
        with TemporaryDirectory() as tmp:
            here = Path(tmp)
            (here / "aws" / ".terraform").mkdir(parents=True)
            with patch.object(lab, "HERE", here), patch.object(lab, "LOCAL", here / ".local"), \
                    patch.object(lab, "terraform", side_effect=subprocess.CalledProcessError(1, "terraform")), \
                    patch.object(lab.subprocess, "run") as run, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    lab.main(["down"])
        run.assert_not_called()

    def test_up_passes_the_bind_address_to_compose_and_checks_health_there(self):
        for bind, check in ((None, "127.0.0.1"), ("0.0.0.0", "127.0.0.1"), ("192.168.5.2", "192.168.5.2")):
            with self.subTest(bind=bind), patch.dict("os.environ", {}, clear=False), \
                    patch.object(lab.start, "main"), patch.object(lab, "wait_healthy") as wait, \
                    patch.object(lab, "prepare") as prepare, patch.object(lab, "terraform"), \
                    patch("sys.stderr", new_callable=io.StringIO) as err:
                lab.main(["up"] + (["--bind", bind] if bind else []))
                self.assertEqual(lab.os.environ["KLOTSE_LAB_BIND"], bind or "127.0.0.1")
            self.assertEqual(wait.call_args.kwargs["host"], check)
            self.assertEqual(prepare.call_args.kwargs["host"], check)
            self.assertEqual("WARNING" in err.getvalue(), bool(bind))

    def test_bind_refuses_a_host_name(self):
        with self.assertRaises(SystemExit):
            lab.bind_address("labhost")


class RemoteTest(unittest.TestCase):
    def test_compose_publishes_on_the_bind_address_defaulting_to_loopback(self):
        text = (Path(lab.HERE) / "compose.yaml").read_text()
        for port in lab.PORTS.values():
            self.assertIn(f'"${{KLOTSE_LAB_BIND:-127.0.0.1}}:{port}:{port}"', text)

    def test_env_for_a_remote_host(self):
        with TemporaryDirectory() as tmp:
            env = lab.environment(Path(tmp), host="10.0.0.5")
        self.assertEqual(env["AWS_ENDPOINT_URL"], "http://10.0.0.5:4566")
        self.assertEqual(env["OCI_CLI_ENDPOINT"], "http://10.0.0.5:4599")
        self.assertEqual(env["CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER"], "http://10.0.0.5:4588/container/")
        self.assertEqual(env["KLOTSE_INSECURE_NET"], "true")
        self.assertEqual(env["TF_VAR_CLIENT_HOST_OVERRIDES"], lab.OCI_OVERRIDE)  # Terraform runs on the lab host
        with TemporaryDirectory() as tmp:
            local = lab.environment(Path(tmp))
        self.assertNotIn("KLOTSE_INSECURE_NET", local)
        self.assertEqual(local["CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER"], "http://localhost:4588/container/")

    def test_hosts_are_validated_and_ipv6_is_bracketed(self):
        self.assertEqual(lab.url_host("::1"), "[::1]")
        self.assertEqual(lab.url_host("lab-1.example.test"), "lab-1.example.test")
        for bad in ("-oProxyCommand=x", "a b", "host/path", "", "user@host"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                lab.url_host(bad)

    def test_env_host_prints_notes_and_prepare_skips_openssl_requirement(self):
        with patch.object(lab, "prepare") as prepare, patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            lab.main(["env", "--host", "labhost", "--prepare"])
        self.assertEqual(prepare.call_args.kwargs, {"host": "labhost", "need_openssl": False})
        self.assertIn("export AWS_ENDPOINT_URL=http://labhost:4566", out.getvalue())
        self.assertIn("lab.py tunnel", err.getvalue())

    def test_tunnel_forwards_every_port_on_loopback_and_refuses_options(self):
        argv = lab.tunnel_argv("me@lab", ["aws", "oci"], ssh_port=2222, identity_file="key")
        forwards = [argv[i + 1] for i, part in enumerate(argv) if part == "-L"]
        self.assertEqual(len(forwards), 22)
        self.assertIn("127.0.0.1:4566:127.0.0.1:4566", forwards)
        self.assertIn("127.0.0.1:16752:127.0.0.1:16752", forwards)
        self.assertEqual(argv[-2:], ["--", "me@lab"])
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        for bad in ("-oProxyCommand=x", "me@lab;rm", "a b", ""):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                lab.tunnel_argv(bad, ["aws"])

    @staticmethod
    def tunnel_args():
        return lab.argparse.Namespace(destination="me@lab", providers="gcp", port=None, identity_file=None,
                                      timeout=5, print=False)

    def test_tunnel_waits_through_the_forward_prepares_and_closes(self):
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(lab, "wait_healthy") as wait, patch.object(lab, "prepare") as prepare, \
                patch("sys.stderr", new_callable=io.StringIO):
            lab.tunnel(self.tunnel_args(), popen=MagicMock(return_value=process))
        self.assertEqual(wait.call_args.args[0], ["gcp"])
        prepare.assert_called_once_with(["gcp"], need_openssl=False)
        process.wait.assert_called()
        process.terminate.assert_called_once()  # still running after wait() returned (mocked): closed on the way out

    def test_tunnel_reports_ssh_exiting(self):
        process = MagicMock()
        process.poll.return_value = 255
        process.returncode = 255
        with patch.object(lab, "prepare") as prepare, patch("sys.stderr", new_callable=io.StringIO), \
                self.assertRaises(SystemExit) as stop:
            lab.tunnel(self.tunnel_args(), popen=MagicMock(return_value=process))
        self.assertIn("ssh exited with status 255", str(stop.exception))
        prepare.assert_not_called()

    def test_prepare_on_a_client_without_openssl_skips_oci(self):
        with TemporaryDirectory() as tmp, patch.object(lab.shutil, "which", return_value=None), \
                patch.object(lab, "run") as run, patch("sys.stderr", new_callable=io.StringIO) as err:
            lab.prepare(["gcp", "oci"], Path(tmp), need_openssl=False)
            self.assertFalse(Path(tmp, "oci-config").exists())
            self.assertTrue(Path(tmp, "gcp-token").exists())
        run.assert_not_called()
        self.assertIn("openssl not found", err.getvalue())


if __name__ == "__main__":
    unittest.main()
