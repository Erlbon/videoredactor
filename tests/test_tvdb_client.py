"""
Tests for core/tvdb_client.py. Fully runnable pieces only -- API-calling
functions need network access this sandbox doesn't have, but
group_episodes_into_seasons() is pure logic, and key resolution/error
paths are real code, not network-dependent.
"""

import os
import unittest

from core.tvdb_client import (
    group_episodes_into_seasons, EpisodeInfo, SeasonInfo,
    get_api_key, search_series, get_series_details, TVDBError,
)


class TestGroupEpisodesIntoSeasons(unittest.TestCase):
    def _ep(self, season, episode):
        return EpisodeInfo(season_number=season, episode_number=episode, name="", overview="", aired="")

    def test_basic_grouping_and_counts(self):
        episodes = [self._ep(1, 1), self._ep(1, 2), self._ep(1, 3), self._ep(2, 1), self._ep(2, 2)]
        result = group_episodes_into_seasons(episodes)
        self.assertEqual([(s.season_number, s.episode_count) for s in result], [(1, 3), (2, 2)])

    def test_empty_list_returns_empty(self):
        self.assertEqual(group_episodes_into_seasons([]), [])

    def test_specials_sorted_last(self):
        episodes = [self._ep(0, 1), self._ep(1, 1), self._ep(2, 1)]
        result = group_episodes_into_seasons(episodes)
        self.assertEqual([s.season_number for s in result], [1, 2, 0])

    def test_only_specials(self):
        result = group_episodes_into_seasons([self._ep(0, 1), self._ep(0, 2)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].season_number, 0)
        self.assertEqual(result[0].episode_count, 2)

    def test_scrambled_input_sorted_output(self):
        episodes = [self._ep(3, 1), self._ep(1, 1), self._ep(2, 1)]
        result = group_episodes_into_seasons(episodes)
        self.assertEqual([s.season_number for s in result], [1, 2, 3])

    def test_specials_display_name(self):
        season = SeasonInfo(season_number=0, episode_count=5)
        self.assertEqual(season.display_name, "Specials (5 episodes)")

    def test_regular_season_display_name(self):
        season = SeasonInfo(season_number=2, episode_count=10)
        self.assertEqual(season.display_name, "Season 2 (10 episodes)")

    def test_no_episodes_lost_or_duplicated_across_seasons(self):
        episodes = [self._ep(1, i) for i in range(1, 6)] + [self._ep(2, i) for i in range(1, 4)]
        result = group_episodes_into_seasons(episodes)
        total = sum(s.episode_count for s in result)
        self.assertEqual(total, len(episodes))


class TestGetApiKey(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.pop("TVDB_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["TVDB_API_KEY"] = self._original
        else:
            os.environ.pop("TVDB_API_KEY", None)

    def test_no_key_returns_none(self):
        self.assertIsNone(get_api_key())

    def test_env_var_takes_priority(self):
        os.environ["TVDB_API_KEY"] = "test-key-123"
        self.assertEqual(get_api_key(), "test-key-123")


class TestMissingKeyErrorPaths(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.pop("TVDB_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["TVDB_API_KEY"] = self._original

    def test_search_series_raises_without_key(self):
        with self.assertRaises(TVDBError):
            search_series("Breaking Bad")

    def test_get_series_details_raises_without_key(self):
        with self.assertRaises(TVDBError):
            get_series_details(12345)


if __name__ == "__main__":
    unittest.main()
