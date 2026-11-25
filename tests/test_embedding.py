import unittest

import numpy as np

from app.embedding import (
    EMBEDDING_DIMENSION,
    normalize_vector,
    cosine_similarity,
    embed_documents,
    embed_query
)


class TestEmbedding(unittest.TestCase):

    def setUp(self):

        self.first = [0.0] * EMBEDDING_DIMENSION
        self.second = [0.0] * EMBEDDING_DIMENSION

        self.first[0] = 1.0
        self.second[1] = 1.0

    def test_normalization(self):

        vector = [2.0] * EMBEDDING_DIMENSION

        result = normalize_vector(vector)

        self.assertAlmostEqual(
            np.linalg.norm(result),
            1.0,
            places=6
        )

    def test_same_vector(self):

        self.assertAlmostEqual(
            cosine_similarity(
                self.first,
                self.first
            ),
            1.0,
            places=6
        )

    def test_orthogonal_vectors(self):

        self.assertAlmostEqual(
            cosine_similarity(
                self.first,
                self.second
            ),
            0.0,
            places=6
        )

    def test_zero_vector(self):

        with self.assertRaises(ValueError):

            normalize_vector(
                [0.0] * EMBEDDING_DIMENSION
            )

    def test_invalid_dimension(self):

        with self.assertRaises(ValueError):

            normalize_vector([1.0, 2.0])

    def test_invalid_values(self):

        vector = self.first.copy()

        vector[0] = float("nan")

        with self.assertRaises(ValueError):

            normalize_vector(vector)

    def test_empty_query(self):

        with self.assertRaises(ValueError):

            embed_query("   ")

    def test_empty_documents(self):

        self.assertEqual(
            embed_documents([]),
            []
        )


if __name__ == "__main__":

    unittest.main() 