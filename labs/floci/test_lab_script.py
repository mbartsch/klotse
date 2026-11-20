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
        with self.assertRaises(SystemExit):
            lab.wait_healthy(["gcp"], timeout=10, opener=MagicMock(side_effect=OSError), sleep=MagicMock(),
                             clock=lambda: next(clock))


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


if __name__ == "__main__":
    unittest.main()
