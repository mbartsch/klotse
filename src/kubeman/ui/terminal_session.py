"""Standalone macOS session runner; runs without importing Qt or the package."""
import json
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


if __name__ == "__main__":
    app, command_json, *target = sys.argv[1:]
    sys.exit(run_session(json.loads(command_json), app, target))
