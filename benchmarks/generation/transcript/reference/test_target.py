from target import total_tokens


def test_nested_usage():
    assert total_tokens({"message": {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 42}}}) == 15


def test_root_usage_is_ignored():
    assert total_tokens({"usage": {"input_tokens": 10, "output_tokens": 5}}) == 0


def test_missing_counts():
    assert total_tokens({"message": {"usage": {"input_tokens": 3}}}) == 3
