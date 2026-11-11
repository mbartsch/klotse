"""Standalone external-terminal session runner; runs without importing Qt or the package."""
import json
from pathlib import Path
import subprocess
import sys


TERMINAL_CLOSE_SCRIPT = '''on run argv
 delay 1
 if application "Terminal" is not running then return
 with timeout of 10 seconds
 tell application "Terminal"
  repeat with targetWindow in windows
   if (id of targetWindow as text) is (item 1 of argv) then
    repeat with targetTab in tabs of targetWindow
     if tty of targetTab is (item 2 of argv) then
      repeat 40 times
       if not busy of targetTab then
        if (count of tabs of targetWindow) is 1 then close targetWindow saving no
        return
       end if
       delay 0.25
      end repeat
      return
     end if
    end repeat
   end if
  end repeat
 end tell
 end timeout
end run'''

ITERM_CLOSE_SCRIPT = '''on run argv
 delay 1
 if application id "com.googlecode.iterm2" is not running then return
 with timeout of 10 seconds
 tell application "iTerm2"
  set windowID to (item 1 of argv) as integer
  if not (exists window id windowID) then return
  repeat with targetTab in tabs of window id windowID
   repeat with targetSession in sessions of targetTab
    if unique ID of targetSession is (item 2 of argv) then
     close targetSession
     return
    end if
   end repeat
  end repeat
 end tell
 end timeout
end run'''


def run_session(command, app, target):
    script = {"Terminal": TERMINAL_CLOSE_SCRIPT, "iTerm2": ITERM_CLOSE_SCRIPT}[app]
    try:
        status = subprocess.call(command)
    except OSError as error:
        print(f"Could not start command: {error}", flush=True)
        status = 127
    except KeyboardInterrupt:
        status = 130
    close_session = status == 0
    if not close_session:
        print(f"\nCommand exited with status {status}. Press Enter to close.", flush=True)
        try:
            input()
            close_session = True
        except (EOFError, KeyboardInterrupt):
            pass
    if close_session:
        # Detach before this runner exits so cleanup survives the terminal shell.
        subprocess.Popen(["/usr/bin/osascript", "-e", script, *target],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return status


def _windows_console():
    """A frozen GUI executable has no console; a source runner gets one from CREATE_NEW_CONSOLE."""
    import ctypes

    ctypes.windll.kernel32.AllocConsole()  # harmlessly fails if the source Python already has its new console
    sys.stdin = open("CONIN$", "r", encoding="utf-8")
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)


def run_windows_session(path):
    """Read argv from a private file, execute it without a shell, and retain the console until Enter."""
    path = Path(path)
    try:
        command = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("The terminal command must be a nonempty list of strings.")
    try:
        status = subprocess.call(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    except OSError as error:
        print(f"Could not start command: {error}", flush=True)
        status = 127
    except KeyboardInterrupt:
        status = 130
    print(f"\nSession ended (status {status}). Press Enter to close.", flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    return status


def main(argv):
    """argv: Windows COMMAND_FILE, or macOS APP COMMAND_JSON [TARGET...]; packaged apps use --terminal-session."""
    if argv[0] == "Windows":
        _windows_console()
        return run_windows_session(argv[1])
    app, command_json, *target = argv
    return run_session(json.loads(command_json), app, target)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
