"""Links into the AWS console, for people who use several AWS accounts.

With one account (or when already signed in to the right one) the plain link is enough. With several accounts the link has
to say which account to be in:

* IAM Identity Center (AWS's documented shortcut links): `<start URL>/#/console?account_id=...&role_name=...&destination=...`
  signs in to the account with that permission set and lands on the destination page.
* Switching role: AWS's switch-role page takes the account and role name but, as far as AWS documents, no destination, so
  the link to the instance is put on the clipboard for the user to paste after switching.

Which account a cluster is in comes from its kubeconfig context when that is the EKS ARN that `aws eks update-kubeconfig`
makes (it contains the account id), or from the plugin setting that maps context names to account ids.
"""
import re
import urllib.parse

from klotse.plugins import Copy, Message, OpenUrl

CONTEXT_ARN = re.compile(r"^arn:aws[a-z-]*:eks:[a-z0-9-]+:(?P<account>\d{12}):cluster/")
ACCOUNT_ID = re.compile(r"\d{12}")


def account_from_context(context):
    """The AWS account id in an EKS ARN context name, else None."""
    match = CONTEXT_ARN.match(str(context or ""))
    return match["account"] if match else None


def parse_pairs(text, valid=None):
    """{name: value} from "name=value" entries separated by new lines (or ; or ,); entries that are not one, or whose value
    `valid` rejects, are skipped."""
    mapping = {}
    for entry in re.split(r"[;,\n]", str(text or "")):
        name, _, value = entry.partition("=")
        name, value = name.strip(), value.strip()
        if name and value and (valid is None or valid(value)):
            mapping[name] = value
    return mapping


def parse_account_map(text):
    """{context name: account id} from "context=123456789012" entries (bad ones are skipped)."""
    return parse_pairs(text, lambda value: ACCOUNT_ID.fullmatch(value) is not None)


def profile_for(context, settings):
    """The AWS profile for a cluster: what was set for it in the Plugins menu, else its entry in "AWS profile for each
    cluster", else the default profile ("" = the default credentials)."""
    return (str(settings.get("cluster_profile") or "").strip() or parse_pairs(settings.get("profile_map")).get(context)
            or str(settings.get("aws_profile") or ""))


def account_for(context, settings):
    """The AWS account of a cluster: set in the Plugins menu, else in "Account for each cluster", else the EKS ARN."""
    chosen = str(settings.get("cluster_account") or "").strip()
    if ACCOUNT_ID.fullmatch(chosen):
        return chosen
    return parse_account_map(settings.get("account_map")).get(context) or account_from_context(context)


def sso_link(start_url, account, role, destination):
    base = start_url.strip().rstrip("/")
    if not base.endswith("/start"):
        base += "/start"  # accepts "https://d-123.awsapps.com" as well as ".../start"
    return (f"{base}/#/console?account_id={urllib.parse.quote(account)}&role_name={urllib.parse.quote(role, safe='')}"
            f"&destination={urllib.parse.quote(destination, safe='')}")


def switch_role_link(account, role, gov=False):
    host = "signin.amazonaws-us-gov.com" if gov else "signin.aws.amazon.com"
    return (f"https://{host}/switchrole?account={urllib.parse.quote(account)}&roleName={urllib.parse.quote(role, safe='/')}"
            f"&displayName={urllib.parse.quote('Klotse ' + account)}")


def console_results(console_url, context, settings, gov=False):
    """What the "Open in the EC2 console" action returns: the plain link, or what is needed to reach the right account."""
    if not settings.get("multi_account"):
        return [OpenUrl(console_url)]
    account = account_for(context, settings)
    if not account:
        return [Message(f"I cannot tell which AWS account the cluster {context} is in. Add it to \"Account for each cluster\" "
                        f"in the plugin's settings, like {context}=123456789012.", "warning")]
    start, role = str(settings.get("sso_start_url") or "").strip(), str(settings.get("role_name") or "").strip()
    if start:
        if not start.lower().startswith("https://"):
            return [Message("The IAM Identity Center start URL must begin with https://", "warning")]
        if not role:
            return [Message("Set the role name (the IAM Identity Center permission set) in the plugin's settings.", "warning")]
        return [OpenUrl(sso_link(start, account, role, console_url))]
    if role:
        return [Copy(console_url), OpenUrl(switch_role_link(account, role, gov)),
                Message(f"Switch to the role {role} in account {account}, then paste the link (it is on the clipboard).")]
    return [Message("You said you use several AWS accounts: set the IAM Identity Center start URL, or a role name to "
                    "switch to, in the plugin's settings.", "warning")]
