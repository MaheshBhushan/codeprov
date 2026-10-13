"""Conservative declared provenance from terminal Git trailers and manual evidence."""
import os
import re
import subprocess


_EMAIL = re.compile(r"^[^<>\r\n]+<([^<>\s]+@[^<>\s]+)>$")


def classify(message, identities, manual_evidence=None):
    """Manual records independently verify unknown commits; disagreements stay explicit.

    A configured identity must exactly match a Co-authored-by trailer email.
    Neither an author name nor an ordinary human coauthor establishes provenance.
    """
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    parsed = subprocess.run(
        ["git", "-c", "trailer.separators=:", "interpret-trailers", "--parse", "--no-divider"],
        input=message, text=True, capture_output=True, check=True, cwd="/", env=env,
    ).stdout
    identities = {identity.lower() for identity in identities}
    trailers = []
    matched = []
    for line in parsed.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip().lower() != "co-authored-by":
            continue
        value = value.strip()
        match = _EMAIL.fullmatch(value)
        email = match.group(1).lower() if match else None
        trailers.append({"value": value, "email": email})
        if email in identities:
            matched.append(email)
    evidence = {"method": "terminal_git_trailers", "coauthors": trailers,
                "matched_identities": sorted(set(matched)), "manual": manual_evidence,
                "conflict": False}
    provenance = "declared_ai" if matched else "unknown"
    if manual_evidence is not None:
        text = manual_evidence.get("evidence") if isinstance(manual_evidence, dict) else manual_evidence
        if not isinstance(text, str) or not text.strip():
            raise ValueError("manual evidence must contain a nonempty independent evidence description")
        if provenance == "unknown":
            provenance = "verified_manual"
        else:
            provenance = "unknown"
            evidence["conflict"] = True
            evidence["conflict_reason"] = "Declared AI trailer conflicts with independent manual-authorship evidence"
    return {"provenance": provenance, "evidence": evidence}
