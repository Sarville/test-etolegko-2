import unittest
from backend.shares import distribute

class ShareTests(unittest.TestCase):
    def test_locked_share_and_removed_offer(self):
        offers = [dict(id=1, share=40, locked=True, removed=False), dict(id=2, share=30, locked=False, removed=False), dict(id=3, share=30, locked=False, removed=False), dict(id=4, share=0, locked=False, removed=True)]
        self.assertEqual([x['share'] for x in distribute(offers)], [40, 30, 30, 0])
        offers[1]['removed'] = True
        self.assertEqual([x['share'] for x in distribute(offers)], [40, 0, 60, 0])

    def test_even_rounding(self):
        self.assertEqual([x['share'] for x in distribute([dict(id=i, share=0, locked=False, removed=False) for i in range(3)])], [34, 33, 33])

    def test_impossible_lock(self):
        with self.assertRaises(ValueError):
            distribute([dict(id=1, share=20, locked=True, removed=False)])
