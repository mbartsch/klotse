"""Which colour a status word gets in a list: healthy (ok), in progress or degraded (warn), failing (bad), or none.

Pure text matching, no Qt. A status is checked for failing words first, then in-progress ones, then healthy ones, so
`Ready,SchedulingDisabled` and `Init:CrashLoopBackOff` are never shown as healthy. Unknown words get no colour.
"""

BAD = ("fail", "error", "crashloop", "backoff", "oomkilled", "evicted", "notready", "unknown", "lost", "invalid",
       "unhealthy", "deadline", "imageinspect", "createcontainer", "outofmemory", "imagepull", "errimage")
WARN = ("pending", "creating", "terminating", "init:", "podinitializing", "schedulingdisabled", "released",
        "waiting", "progress", "suspended", "unschedulable", "unavailable", "degraded")
OK = ("running", "ready", "active", "bound", "available", "complete", "succeeded", "true", "healthy", "established")

TONE_ATTRS = ("status", "phase")  # the columns that get coloured


def status_tone(text):
    word = str(text).strip().lower()
    if not word:
        return None
    for tone, words in (("bad", BAD), ("warn", WARN), ("ok", OK)):
        if any(w in word for w in words):
            return tone
    return None
