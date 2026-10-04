import queue
import unittest
import weakref

import numpy as np

from latest_jobs import LatestJobQueue


class LatestJobsTest(unittest.TestCase):
    def test_click_burst_retains_one_frame_and_releases_superseded_frames(self):
        jobs = LatestJobQueue()
        references = []
        for number in range(1000):
            frame = np.full((36,64,3), number % 255, np.uint8)
            references.append(weakref.ref(frame))
            jobs.put((number, {"screenshot":frame}))
        self.assertEqual(jobs.qsize(), 1)
        self.assertEqual(sum(ref() is not None for ref in references), 1)
        self.assertEqual(jobs.get_nowait()[0], 999)
        self.assertTrue(jobs.empty())
        with self.assertRaises(queue.Empty):
            jobs.get_nowait()

    def test_late_old_art_job_cannot_replace_new_card_job(self):
        jobs = LatestJobQueue()
        jobs.put((2,"new"))
        jobs.put((1,"old"))
        self.assertEqual(jobs.get_nowait(), (2,"new"))
        jobs.put((1,"late old"))
        self.assertTrue(jobs.empty())
        jobs.put((2,"new refreshed"))
        self.assertEqual(jobs.get_nowait(), (2,"new refreshed"))
