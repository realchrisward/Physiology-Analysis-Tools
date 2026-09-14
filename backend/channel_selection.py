import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelRule:
    pattern: re.Pattern
    target_channel: str


# Confirmed against real sample files in
# src/physiology_analysis_tools/examples/ECG_traces. Edit this table if a
# new filename convention is confirmed later - nothing else needs to change.
CHANNEL_RULES: list[ChannelRule] = [
    ChannelRule(re.compile(r"^\d+$"), "channel 1"),
    ChannelRule(re.compile(r"^M\d+$", re.IGNORECASE), "channel 2"),
    ChannelRule(re.compile(r"^D\d+$", re.IGNORECASE), "ekg"),
]

# Different extractors name the time column differently (labchart_text_extract
# uses "ts", adi_extract uses "time") - exclude both, plus "comment".
NON_SIGNAL_CHANNELS: set[str] = {"ts", "time", "comment"}


@dataclass(frozen=True)
class ChannelSelection:
    channel: str | None
    matched_rule: bool


def select_default_channel(
    filename: str, available_channels: list[str]
) -> ChannelSelection:
    stem = os.path.splitext(os.path.basename(filename))[0]
    by_lower = {c.lower(): c for c in available_channels}

    for rule in CHANNEL_RULES:
        if rule.pattern.match(stem):
            matched = by_lower.get(rule.target_channel.lower())
            if matched is not None:
                return ChannelSelection(channel=matched, matched_rule=True)

    for c in available_channels:
        if c.lower() not in NON_SIGNAL_CHANNELS:
            return ChannelSelection(channel=c, matched_rule=False)

    return ChannelSelection(channel=None, matched_rule=False)
