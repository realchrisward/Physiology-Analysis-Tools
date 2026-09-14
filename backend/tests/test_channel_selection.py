from backend.channel_selection import select_default_channel


def test_numeric_filename_defaults_to_channel_1():
    result = select_default_channel(
        "556420.adicht", ["channel 1", "time", "comment"]
    )

    assert result.channel == "channel 1"
    assert result.matched_rule is True


def test_m_prefixed_filename_defaults_to_channel_2():
    result = select_default_channel(
        "M00561499.adicht",
        ["channel 1", "channel 2", "channel 3", "time", "comment"],
    )

    assert result.channel == "channel 2"
    assert result.matched_rule is True


def test_d_prefixed_filename_defaults_to_ekg():
    result = select_default_channel(
        "D48136.adicht", ["ekg", "hr", "rr", "time", "comment"]
    )

    assert result.channel == "ekg"
    assert result.matched_rule is True


def test_channel_matching_is_case_insensitive():
    result = select_default_channel(
        "d48136.adicht", ["EKG", "HR", "RR", "TIME", "COMMENT"]
    )

    assert result.channel == "EKG"
    assert result.matched_rule is True


def test_unrecognized_filename_falls_back_to_first_signal_channel():
    result = select_default_channel(
        "weird_name.txt", ["comment", "ts", "raw_signal"]
    )

    assert result.channel == "raw_signal"
    assert result.matched_rule is False


def test_no_signal_channels_returns_none():
    result = select_default_channel("123.adicht", ["time", "comment"])

    assert result.channel is None
    assert result.matched_rule is False
