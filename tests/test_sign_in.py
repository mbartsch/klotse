"""The sign-in command for a kubeconfig's exec helper: `aws sso login` (with the profile) for AWS, the helper itself otherwise."""
from klotse.k8s.sign_in import sign_in_argv


def test_aws_signs_in_with_sso_and_keeps_the_profile():
    info = {"exec": "aws", "args": ["eks", "get-token", "--cluster-name", "eu", "--profile", "work"]}
    assert sign_in_argv(info) == ["aws", "sso", "login", "--profile", "work"]
    assert sign_in_argv({"exec": "aws", "args": ["eks", "get-token", "--profile=dev"]}) == ["aws", "sso", "login", "--profile", "dev"]
    assert sign_in_argv({"exec": "aws", "args": ["eks", "get-token"]}) == ["aws", "sso", "login"]


def test_an_aws_program_with_a_full_path_is_still_treated_as_aws():
    info = {"exec": "C:/tools/aws.exe", "args": ["eks", "get-token", "--profile", "dev"]}
    assert sign_in_argv(info) == ["C:/tools/aws.exe", "sso", "login", "--profile", "dev"]


def test_another_helper_runs_with_its_own_arguments():
    info = {"exec": "kubelogin", "args": ["get-token", "--oidc-issuer-url", "https://issuer.test"]}
    assert sign_in_argv(info) == ["kubelogin", "get-token", "--oidc-issuer-url", "https://issuer.test"]


def test_no_helper_means_nothing_to_run():
    assert sign_in_argv({"exec": "", "args": []}) is None
    assert sign_in_argv({"server": "https://a.test"}) is None
