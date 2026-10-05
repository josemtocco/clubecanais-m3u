import unittest
from gerar_m3u import parse_sidebar_categories, parse_category_page, extract_streams, dedupe, Channel

INDEX='https://clubecanais.com.br/index.php'
class TestDiscovery(unittest.TestCase):
    def test_sidebar_categories(self):
        html='''<a href="index.php?category=40">EVANGÉLICA</a><a href="index.php?category=51">VARIEDADES</a>'''
        cats=parse_sidebar_categories(html)
        self.assertEqual([c.name for c in cats],['EVANGÉLICA','VARIEDADES'])
    def test_category_channels(self):
        html='''<a href="channel.php?id=123">Canal A EVANGÉLICA Brasil</a><a href="channel.php?id=124">Canal B EVANGÉLICA Brasil</a>'''
        d=parse_category_page(html,type('C',(),{'name':'EVANGÉLICA','url':INDEX})())
        self.assertEqual(set(d),{'123','124'}); self.assertEqual(d['123'].category,'EVANGÉLICA')
    def test_stream_extraction(self):
        html='''<script>const source="https://cdn.example.com/live/test/playlist.m3u8";</script>'''
        self.assertEqual(extract_streams(html,INDEX),['https://cdn.example.com/live/test/playlist.m3u8'])
    def test_dedupe(self):
        a=Channel('1','A','X',stream='https://a/playlist.m3u8',validation='retained_ok')
        b=Channel('1','A','X',stream='https://a/playlist.m3u8',validation='ok')
        self.assertEqual(len(dedupe([a,b])),1); self.assertEqual(dedupe([a,b])[0].validation,'ok')
if __name__=='__main__': unittest.main()
