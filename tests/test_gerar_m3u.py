import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gerar_m3u as app

class PlaylistTests(unittest.TestCase):
    def test_dedupe_keeps_channels_and_prefers_validated(self):
        a = app.Channel('1', 'Canal A', 'NOTÍCIAS', stream='https://x/a.m3u8', validation='seed')
        b = app.Channel('1', 'Canal A', 'NOTÍCIAS', stream='https://x/a.m3u8', validation='ok')
        c = app.Channel('2', 'Canal B', 'ESPORTES', stream='https://x/b.m3u8', validation='ok')
        got = app.dedupe_channels([a, b, c])
        self.assertEqual({x.id for x in got}, {'1', '2'})
        self.assertEqual(next(x for x in got if x.id == '1').validation, 'ok')

    def test_m3u_contains_names_and_categories(self):
        ch = app.Channel('7', 'TV Teste', 'CULTURA', logo='https://x/logo.png', stream='https://x/live.m3u8')
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'out.m3u'
            with patch.object(app, 'OUT_M3U', target):
                app.write_m3u([ch])
            text = target.read_text(encoding='utf-8')
            self.assertIn('tvg-name="TV Teste"', text)
            self.assertIn('group-title="CULTURA"', text)
            self.assertIn('https://x/live.m3u8', text)

    def test_seed_has_eight_known_channels(self):
        data = json.loads(Path('canais-seed.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['channels']), 8)
        self.assertTrue(all(x.get('id') and x.get('stream') for x in data['channels']))

if __name__ == '__main__':
    unittest.main(verbosity=2)
