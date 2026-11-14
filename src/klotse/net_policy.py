"""Network policy switches shared by the cloud plugins (pure: reads the environment only).

KLOTSE_INSECURE_NET=true lets cloud endpoints (AWS_ENDPOINT_URL) be plain http:// to any host, so requests and their signatures
travel unencrypted. It exists for testing against local emulators such as Floci while the plugins are being verified; it is
meant to be removed for 1.0.0. Off by default."""
import os

INSECURE_NET_VARIABLE = "KLOTSE_INSECURE_NET"
TRUE_VALUES = ("1", "true", "yes", "on")


def insecure_net_allowed(environ=None):
    """Whether KLOTSE_INSECURE_NET is set to a true value (1, true, yes or on, any case). Anything else is off."""
    value = (os.environ if environ is None else environ).get(INSECURE_NET_VARIABLE, "")
    return str(value).strip().lower() in TRUE_VALUES
