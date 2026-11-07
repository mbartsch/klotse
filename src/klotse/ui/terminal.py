import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid


NODE_SHELL_IMAGE = "docker.io/library/alpine"


def kubectl_base(context, kubeconfig=None):
    """kubectl with an explicit context, and an explicit --kubeconfig when a custom one is selected."""
    return ["kubectl"] + (["--kubeconfig", kubeconfig] if kubeconfig else []) + ["--context", context]


def kubectl_args(action, context, namespace, pod, container=None, kubeconfig=None):
    """argv for an interactive kubectl session. action: 'shell' (exec) or 'attach'."""
    base = kubectl_base(context, kubeconfig) + ["-n", namespace]
    if action == "shell":
        cmd = base + ["exec", "-it", pod] + (["-c", container] if container else []) + ["--", "sh"]
    elif action == "attach":
        cmd = base + ["attach", "-it", pod] + (["-c", container] if container else [])
    else:
        raise ValueError(f"Unknown action {action}")
    return cmd


DEBUG_IMAGE = "docker.io/library/busybox"


def debug_args(context, namespace, pod, image, target=None, name=None, kubeconfig=None):
    """argv for `kubectl debug`: an interactive ephemeral container with `image` in the pod, sharing the process namespace of
    `target` (a container of the pod) when one is named. Ephemeral containers cannot be removed from a pod afterwards."""
    cmd = kubectl_base(context, kubeconfig) + ["-n", namespace, "debug", "-it", pod, f"--image={image}"]
    if target:
        cmd.append(f"--target={target}")
    if name:
        cmd.append(f"--container={name}")
    return cmd + ["--", "sh"]


def node_shell_args(context, node, namespace="default", image=NODE_SHELL_IMAGE, kubeconfig=None):
    """Linux defaults from the installed kubectl-node-shell v1.11.0 script.

    Use kubectl's attached run/--rm lifecycle so this works on all local
    platforms without installing the shell plugin or requiring a local shell.
    """
    pod = f"nsenter-{uuid.uuid4().hex[:12]}"
    overrides = {"spec": {
        "nodeName": node,
        "hostPID": True,
        "hostIPC": True,
        "hostNetwork": True,
        "imagePullSecrets": None,
        "containers": [{
            "name": "nsenter", "image": image,
            "securityContext": {"privileged": True},
            "stdin": True, "stdinOnce": True, "tty": True,
            "command": ["nsenter", "--target", "1", "--mount", "--uts", "--ipc",
                        "--net", "--pid", "bash", "-l"],
            "resources": {}, "volumeMounts": [],
        }],
        "tolerations": [
            {"key": "CriticalAddonsOnly", "operator": "Exists"},
            {"effect": "NoExecute", "operator": "Exists"},
        ],
        "volumes": [],
    }}
    return kubectl_base(context, kubeconfig) + ["--namespace", namespace,
            "run", pod, "--image", image, "--restart=Never", "--rm", "-it",
            "--pod-running-timeout=1m", "--overrides", json.dumps(overrides)]


def _mac_session_command(argv, mac_terminal):
    if getattr(sys, "frozen", False):
        # Packaged app: there is no separate Python or script file, so the app binary itself runs the session.
        return "exec " + shlex.join([sys.executable, "--terminal-session", mac_terminal, json.dumps(argv)])
    # Replace the login shell; the standalone runner closes only its own session.
    runner = str(Path(__file__).with_name("terminal_session.py").resolve())
    return "exec " + shlex.join([sys.executable, runner, mac_terminal, json.dumps(argv)])


def open_terminal(argv, mac_terminal="Terminal"):
    """Open an interactive session; macOS sessions close after a successful command."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k"] + argv, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        # Pass the shell command as an AppleScript argument, never as script source.
        if mac_terminal == "Terminal":
            command = _mac_session_command(argv, mac_terminal)
            script = ('on run argv\n tell application "Terminal"\n activate\n'
                      ' set shellTab to do script ""\n'
                      ' set shellWindowID to id of front window\n'
                      ' set targetTTY to tty of shellTab\n'
                      ' do script ((item 1 of argv) & " " & quoted form of (shellWindowID as text)'
                      ' & " " & quoted form of targetTTY) in shellTab\n end tell\nend run')
        elif mac_terminal == "iTerm2":
            command = _mac_session_command(argv, mac_terminal)
            script = ('on run argv\n tell application "iTerm2"\n activate\n'
                      ' set shellWindow to (create window with default profile)\n'
                      ' set shellWindowID to id of shellWindow\n'
                      ' tell current session of shellWindow\n'
                      ' set sessionID to unique ID\n'
                      ' write text ((item 1 of argv) & " " & quoted form of (shellWindowID as text)'
                      ' & " " & quoted form of sessionID)\n'
                      ' end tell\n end tell\nend run')
        else:
            raise ValueError(f"Unsupported macOS terminal: {mac_terminal}")
        try:
            subprocess.run(["osascript", "-e", script, command], check=True,
                           capture_output=True, text=True, timeout=15)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Could not open {mac_terminal}: {error.stderr or error}") from error
    else:
        command = (shlex.join(argv) + '; status=$?; printf "\\nSession ended. Press Enter to close."; '
                   'read -r answer; exit "$status"')
        for terminal, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]),
                               ("konsole", ["-e"]), ("xterm", ["-e"])):
            executable = shutil.which(terminal)
            if executable:
                subprocess.Popen([executable, *args, "bash", "-c", command])
                return
        raise RuntimeError("No supported terminal found (x-terminal-emulator, gnome-terminal, konsole or xterm)")
