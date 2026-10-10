#!/usr/bin/env python3
"""Build bench/router/corpus.jsonl — the router bench golden set.

The Phase-0 plan references a "wisp-route-fuzz" corpus of ~180 utterances;
no such file exists in wisp, open-computer, or anywhere on this machine.
This builder constructs the corpus from three provenance classes:

  wisp-case   — the heuristic coverage table in wisp tests/test_route.py
                (hand-verified labels, verbatim)
  wisp-trace  — real fleet transcripts from wisp's decisions.jsonl /
                route_ab.jsonl logs, scrubbed: dictation payloads and
                personal/legal/financial content are excluded, not
                redacted (paraphrasing private speech still leaks it)
  synthetic   — authored utterances filling route/model-tier gaps

Two label axes per fixture:

  route — wisp's production task-type taxonomy (JEV_QUESTIONS["route"]
          criteria): launch, tool, agent, learn, act, dictation, answer,
          clarify. This is the classification exact-match axis.
  tier  — which model tier should serve the task (model-selection axis):
            local    — trivial; a 4B-class local model suffices (free)
            cheap    — flash-class hosted (deepseek-flash, gemini-flash)
            mid      — strong mid-tier (grok-4-fast class)
            frontier — only for hard multi-step reasoning; price ceiling
                       is Grok 4.7, frontier picks must be rare

Tier policy (the placement policy this bench is implicitly testing):
  - launch/tool/dictation/clarify  -> local   (deterministic, no reasoning)
  - answer (simple fact/chat)      -> local
  - answer (explanatory/multi-hop) -> cheap
  - act (single-step on screen)    -> local
  - act (multi-step, conditional)  -> cheap
  - act (complex recovery/debug)   -> mid
  - agent (scoped, well-defined)   -> cheap
  - agent (broad research/design)  -> mid
  - frontier is reserved for cases where cheaper tiers demonstrably
    fail; nothing in a voice-command corpus should need it
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("corpus.jsonl")

C = []  # (utterance, route, tier, source, note)


def add(utt, route, tier, source, note=""):
    C.append((utt, route, tier, source, note))


# ---------------------------------------------------------------- wisp cases
# tests/test_route.py CASES — verbatim (text, route, app)
WISP_CASES = [
    ("open firefox", "launch", "local"),
    ("Open the terminal.", "launch", "local"),
    ("launch Discord", "launch", "local"),
    ("please start firefox", "launch", "local"),
    ("can you open discord", "launch", "local"),
    ("wisp, pull up the terminal app", "launch", "local"),
    ("open firefox and go to github", "act", "local"),
    ("open discord on the settings page", "act", "local"),
    ("open the spreadsheet app", "act", "local"),
    ("click the blue button", "act", "local"),
    ("search for flights to denver", "act", "cheap"),
    ("dictate hello world", "dictation", "local"),
    ("take dictation", "dictation", "local"),
    ("type this meeting starts at noon", "dictation", "local"),
    ("write this down buy milk", "dictation", "local"),
    ("what time is it", "answer", "local"),
    ("how do I open a file in vim", "answer", "cheap"),
    ("why is the sky blue", "answer", "cheap"),
    ("tell me a joke", "answer", "local"),
    ("is it going to rain?", "answer", "local"),
    ("hello there", "answer", "local"),
    ("take a screenshot", "tool", "local"),
    ("go to workspace 3", "tool", "local"),
    ("have an agent research rust async", "agent", "cheap"),
    ("spawn an agent to fix the login bug", "agent", "cheap"),
    ("learn how to export a pdf", "learn", "local"),
    ("remember this workflow", "learn", "local"),
]
for utt, route, tier in WISP_CASES:
    add(utt, route, tier, "wisp-case")

# ------------------------------------------------------- real fleet traces
# Scrubbed selections from ~/.local/share/wisp/decisions.jsonl + route_ab.
# Excluded entirely (privacy): dictation payloads of legal documents,
# financial-account utterances, STT noise containing personal names.
WISP_TRACES = [
    ("I need to listen to some music.", "act", "local"),
    ("Open Discord.", "launch", "local"),
    ("Open steam.", "launch", "local"),
    ("Where is the clock on my screen?", "answer", "local"),
    ("point at the time display in my bar.", "act", "local"),
    ("I need to check on tailscale on my console there", "act", "cheap"),
    ("Let's try to do a test and let's list all of the tailscale active devices.", "act", "cheap"),
    ("I want to open up the tailscale plugin on the top right bar.", "act", "local"),
    ("Open Tailscale plugin on the top right bar.", "act", "local"),
    ("Open the terminal.", "launch", "local"),
    ("What's on my screen?", "answer", "local"),
    ("What is all of this?", "clarify", "local"),
    ("Check on my dayflow MCP and let me know what we accomplished today.", "act", "cheap"),
    ("(helicopter whirring) Let's try opening the terminal.", "launch", "local"),
    ("Open retro arcade.", "launch", "local"),
    ("Now open up Super Mario Bros. on the Nintendo 64 page.", "act", "local"),
    ("Open up retro kid", "launch", "local"),
    ("Open up Nintendo 64.", "act", "local"),
    ("I need to pull up discord and check on the open router discord channel", "act", "local"),
    ("Did you see that open router opened correctly or not?", "answer", "local"),
    ("Now on the left is the terminal and on the right is discord, if it didn't load correctly can you find the crash logs for it?", "act", "mid"),
    ("As you can see on the right side is this group but it didn't load correctly so we need to find the logs.", "act", "cheap"),
    ("That means you need to do it anyway.", "clarify", "local"),
    ("What's the problem?", "answer", "local"),
    ("Discord, I need to check on the open router Discord that I'm in.", "act", "local"),
    ("Discord's not working. What's wrong with Discord?", "act", "cheap"),
    ("Okay, then pull up Steam, let's see what games are installed on Steam and what their overall rating is on the Steam store.", "act", "cheap"),
    ("Let's go look at workspace 4.", "tool", "local"),
    ("open it up in a browser, just do a new tab and open it", "act", "local"),
    ("that's not the browser that i use, i use browseros by the way", "clarify", "local"),
    ("it's open right here just do command t for a new tab or go click on the plus button", "act", "local"),
    ("at the top there in the url bar type in robinhood.com", "act", "local"),
    ("click on gdx, i wanna see gdx", "act", "local"),
    ("nope, you didn't", "clarify", "local"),
    ("open discord", "launch", "local"),
    ("what's going on in my screen literally", "answer", "local"),
    ("this isn't envy, this is herder, h-e-r-d-r, you can list it as the CLI tools", "act", "cheap"),
    ("let's go to workspace 3", "tool", "local"),
    ("Let's test this out with discord", "act", "local"),
    ("Close this tab.", "act", "local"),
    ("hdspmx.com", "act", "local"),
    ("Don't use chromium, use browser os", "act", "local"),
    ("Click on the circle", "act", "local"),
    ("Click on the number dots in order", "act", "local"),
    ("Try it again, that was wrong", "act", "local"),
    ("click on the circle click on the circle click on the circle", "act", "local"),
    ("What's happening on my screen?", "answer", "local"),
    ("What's your name?", "answer", "local"),
    ("What can you do?", "answer", "local"),
    ("click on the red circle", "act", "local"),
    ("click on join waitlist", "act", "local"),
    ("Hit play on spotify", "act", "local"),
    ("click on hermes bots", "act", "local"),
    ("and, what are we gonna do? What are we gonna do?", "clarify", "local"),
    ("Open up the store page for Red Dead Redemption 2", "act", "local"),
    ("and the other one.", "clarify", "local"),
    ("Open design craft", "launch", "local"),
    ("kent kent, kent, kent, kent, kent, kent", "clarify", "local"),
    ("hwmixl, kdenliveoffice,", "clarify", "local"),
    ("nope, you didn't do it", "clarify", "local"),
]
for utt, route, tier in WISP_TRACES:
    add(utt, route, tier, "wisp-trace")

# ------------------------------------------------------------- synthetic
# Fill under-covered routes (learn, dictation, clarify, agent) and add
# compound/adversarial utterances a router must not misclassify.
SYNTH = [
    # launch
    ("open spotify", "launch", "local"),
    ("launch the file manager", "launch", "local"),
    ("start vscode please", "launch", "local"),
    ("pull up the settings app", "launch", "local"),
    ("hey wisp open the browser", "launch", "local"),
    ("close discord", "launch", "local"),
    ("quit the terminal", "launch", "local"),
    # tool
    ("switch to workspace 2", "tool", "local"),
    ("move this window to workspace 5", "tool", "local"),
    ("take a screenshot of this window", "tool", "local"),
    ("run the update script", "tool", "local"),
    ("find my downloads folder", "tool", "local"),
    ("notify me when the build finishes", "tool", "local"),
    ("type my email into that field", "tool", "local"),
    ("mute the microphone", "tool", "local"),
    # agent
    ("delegate this bug to an agent", "agent", "cheap"),
    ("have an agent write tests for the parser", "agent", "cheap"),
    ("spawn a research agent on rust async runtimes", "agent", "mid"),
    ("agent: summarize today's commits across all repos", "agent", "cheap"),
    ("get an agent to triage the open issues on orchestral", "agent", "cheap"),
    ("have an agent design the sync protocol", "agent", "mid"),
    ("delegate the migration plan to an agent", "agent", "mid"),
    ("spawn an agent to compare router benchmarks", "agent", "cheap"),
    # learn
    ("learn how to deploy this app", "learn", "local"),
    ("remember how I set up my monitors", "learn", "local"),
    ("add a skill for restarting tailscale", "learn", "local"),
    ("learn that my editor is neovim", "learn", "local"),
    ("remember this sequence for the standup", "learn", "local"),
    ("teach yourself how to rebuild the kernel module", "learn", "local"),
    # dictation
    ("dictate a message to sam", "dictation", "local"),
    ("take dictation for my notes", "dictation", "local"),
    ("type this: the meeting moved to three", "dictation", "local"),
    ("write down what I say next", "dictation", "local"),
    ("dictation mode", "dictation", "local"),
    # answer
    ("what's the weather tomorrow", "answer", "local"),
    ("who wrote the rust borrow checker", "answer", "cheap"),
    ("explain how git rebase works", "answer", "cheap"),
    ("what does this error message mean", "answer", "cheap"),
    ("how do I reverse a list in python", "answer", "cheap"),
    ("compare the three scheduling algorithms for me", "answer", "cheap"),
    ("what time is my next meeting", "answer", "local"),
    ("why did my last command fail", "answer", "cheap"),
    # act (harder, multi-step / recovery)
    ("open firefox then go to github and find my notifications", "act", "cheap"),
    ("click the settings gear then scroll down to appearance", "act", "local"),
    ("go to the store page and buy the cheapest skin", "act", "cheap"),
    ("find the tab playing music and close it", "act", "local"),
    ("the build failed again, open the logs and tell me why", "act", "mid"),
    ("scroll down on that page until you see the download button then click it", "act", "cheap"),
    ("switch to the browser window and reload it", "act", "local"),
    ("navigate to the billing page and download last month's invoice", "act", "cheap"),
    ("something crashed, find the error in the journal and report it", "act", "mid"),
    ("select all the text in this field and delete it", "act", "local"),
    # clarify (ambiguous / noise / underspecified)
    ("do it", "clarify", "local"),
    ("that thing", "clarify", "local"),
    ("mm hmm", "clarify", "local"),
    ("the blue one", "clarify", "local"),
    ("uhhh", "clarify", "local"),
    ("go over there", "clarify", "local"),
    ("fix it", "clarify", "local"),
    # compound / adversarial — the misclassification traps
    ("open firefox and search for cheap flights", "act", "cheap"),
    ("launch discord then join the voice channel", "act", "local"),
    ("tell me the time then open the calendar", "act", "local"),
    ("what's the weather and should I open the umbrella app", "act", "cheap"),
    ("dictate a note saying open the pod bay doors", "dictation", "local"),
    ("don't open anything just tell me how to do it myself", "answer", "cheap"),
    ("learn this: every time I say morning, open my email", "learn", "local"),
    ("can you remember to buy milk tomorrow", "answer", "local"),
    ("screenshot this then send it to the agent for analysis", "agent", "cheap"),
    ("is discord open right now", "answer", "local"),
    ("open whatever app plays music", "launch", "local"),
    ("click ok then wait for the download to finish then close the window", "act", "cheap"),
    ("ask the agent to review this diff and explain any security issues", "agent", "mid"),
    ("if spotify is open skip the track otherwise open it", "act", "cheap"),
    ("read this error out loud", "answer", "local"),
    # second pass — reach ~180 and cover thin spots
    ("hey wisp", "clarify", "local"),
    ("stop", "clarify", "local"),
    ("cancel that", "clarify", "local"),
    ("nevermind", "clarify", "local"),
    ("open the app store and install the update for discord", "act", "cheap"),
    ("go back", "act", "local"),
    ("scroll down", "act", "local"),
    ("refresh this page", "act", "local"),
    ("zoom in on the map", "act", "local"),
    ("close all the browser tabs except this one", "act", "cheap"),
    ("have an agent audit the auth module for leaked secrets", "agent", "mid"),
    ("spawn an agent to reproduce that flaky test", "agent", "cheap"),
    ("learn that deploys go through the staging branch first", "learn", "local"),
    ("dictate a reply to the pull request comment", "dictation", "local"),
    ("how many workspaces do I have open right now", "answer", "local"),
    ("what did the last command output", "answer", "local"),
    ("summarize the article in this tab", "answer", "cheap"),
    ("translate the selected text to spanish", "act", "cheap"),
    ("move this window to the right half of the screen", "tool", "local"),
    ("open the file I was just editing in vscode", "act", "local"),
]
for utt, route, tier in SYNTH:
    add(utt, route, tier, "synthetic")

# ------------------------------------------------------------------ emit
seen = set()
rows = []
for i, (utt, route, tier, source, note) in enumerate(C, 1):
    key = utt.strip().lower()
    if key in seen:
        continue
    seen.add(key)
    rows.append({
        "id": f"rb-{len(rows) + 1:03d}",
        "utterance": utt.strip(),
        "expected_route": route,
        "expected_tier": tier,
        "source": source,
        **({"note": note} if note else {}),
    })

OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))

from collections import Counter

print(f"{len(rows)} fixtures -> {OUT}")
print("routes:", dict(Counter(r['expected_route'] for r in rows)))
print("tiers:", dict(Counter(r['expected_tier'] for r in rows)))
print("sources:", dict(Counter(r['source'] for r in rows)))
