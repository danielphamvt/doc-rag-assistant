"""Tests for the greeting router predicate (no LLM / no embeddings needed)."""
from rag_agent.routing import is_greeting_message, normalize_message


def test_short_legal_question_is_not_a_greeting():
    # Regression: "khi" contains "hi" and used to be misrouted by substring matching.
    assert not is_greeting_message("Thừa kế khi nào?")
    assert not is_greeting_message("Luật đất đai quy định gì về chuyển nhượng?")
    assert not is_greeting_message("Tôi muốn hỏi về luật hôn nhân")


def test_nfd_decomposed_input_is_recognized():
    # macOS keyboards may emit NFD; "hình sự" decomposed contains ASCII "hi".
    assert "hi" in "hình sư"  # proves the old substring trap
    assert not is_greeting_message("hình sư là gì?")

    nfd_greeting = "xin chào"  # decomposed "xin chào"
    assert normalize_message(nfd_greeting) == "xin chào"
    assert is_greeting_message(nfd_greeting)


def test_greetings_and_farewells_are_recognized():
    for text in ["hi", "Hello!", "HELLO", "xin chào", "Xin chào bạn.",
                 "thank you", "tạm biệt", "bye"]:
        assert is_greeting_message(text), text


def test_messages_with_extra_words_are_not_greetings():
    # Small-talk plus an actual question must go through the normal pipeline.
    assert not is_greeting_message("cảm ơn nhé, cho tôi hỏi về luật đất đai")
    assert not is_greeting_message("hi, tôi muốn hỏi về thuế thu nhập cá nhân")


def test_empty_and_none_are_not_greetings():
    assert not is_greeting_message("")
    assert not is_greeting_message(None)
