"""Canned demo messages so the MCP server works with zero setup.

Edit freely -- add threads, vary dates, drop messages into other folders to
exercise list/move behaviour while you build out the agent side.
"""
from .backends import Message


def load_demo_messages() -> list[Message]:
    return [
        Message(
            id="INBOX:1",
            folder="INBOX",
            sender="Maya Okafor <maya@acme.example>",
            subject="Q3 planning doc - needs your review",
            date="2026-06-15T09:12:00",
            unread=True,
            snippet="Dropping the Q3 planning doc here. Could you look at the staffing section...",
            body=("Hi,\n\nDropping the Q3 planning doc here. Could you look at the "
                  "staffing section before Thursday's sync? Mainly the headcount "
                  "asks.\n\nThanks,\nMaya"),
        ),
        Message(
            id="INBOX:2",
            folder="INBOX",
            sender="billing@cloudhost.example",
            subject="Your invoice for June is ready",
            date="2026-06-14T06:00:00",
            unread=True,
            snippet="Invoice #4471 for the period June 1-30 is now available...",
            body="Invoice #4471 for the period June 1-30 is now available. Total due: $182.40.",
        ),
        Message(
            id="INBOX:3",
            folder="INBOX",
            sender="Jonas Reuter <j.reuter@partner.example>",
            subject="Re: contract redlines",
            date="2026-06-13T17:45:00",
            unread=False,
            snippet="Thanks for the turnaround. One open point on clause 7...",
            body=("Thanks for the turnaround. One open point on clause 7 - can we "
                  "cap liability at fees paid in the prior 12 months? Happy to hop "
                  "on a call.\n\nJonas"),
        ),
        Message(
            id="Newsletters:1",
            folder="Newsletters",
            sender="The Saturday Brief <hello@brief.example>",
            subject="This week: 7 things in AI infra",
            date="2026-06-14T08:00:00",
            unread=True,
            snippet="Your weekly roundup. Top story: the new wave of agent harnesses...",
            body="Your weekly roundup. Top story: the new wave of agent harnesses.",
        ),
    ]
