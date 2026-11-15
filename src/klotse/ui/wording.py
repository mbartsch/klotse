"""Words for counts and kinds in messages. Pure text, no Qt."""


def plural_label(kind):
    """'NodePool' -> 'NodePools', 'EC2NodeClass' -> 'EC2NodeClasses', 'NetworkPolicy' -> 'NetworkPolicies'."""
    if kind.endswith("y") and not kind.endswith(("ay", "ey", "oy", "uy")):
        return kind[:-1] + "ies"
    return kind + ("es" if kind.endswith(("s", "x", "ch", "sh")) else "s")


def counted(n, kind):
    """'1 Ingress', '2 Ingresses'."""
    return f"{n} {kind if n == 1 else plural_label(kind)}"


def menu_text(text):
    """A menu caption without its mnemonic markers: 'Logs &amp;&amp; activity' keeps its ampersand, '&amp;Logs' loses the marker."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")
