import unittest

from pydantic import ValidationError

from app.schemas import SearchRequest, SearchMode


class TestSearchRequest(unittest.TestCase):

    def test_valid_query(self):
        request = SearchRequest(query="Redis连接超时")

        self.assertEqual(
            request.mode,
            SearchMode.ADAPTIVE
        )

    def test_empty_query(self):
        with self.assertRaises(ValidationError):
            SearchRequest(query="   ")

    def test_invalid_top_k(self):
        with self.assertRaises(ValidationError):
            SearchRequest(
                query="Redis",
                top_k=100
            )

    def test_invalid_mode(self):
        with self.assertRaises(ValidationError):
            SearchRequest(
                query="Redis",
                mode="unknown"
            )

    def test_unknown_field(self):
        with self.assertRaises(ValidationError):
            SearchRequest(
                query="Redis",
                unknown_field=True
            )


if __name__ == "__main__":
    unittest.main()