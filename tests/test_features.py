import sys,json,tempfile,dataclasses,wave,struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_export import Tests as Fixture
from core import *
from effects import *
from preferences import Preferences
import unittest

class Features(unittest.TestCase):
    setUp=Fixture.setUp
    tearDown=Fixture.tearDown
    export=Fixture.export
    # Reuse the temporary-audio fixture, not duplicate inherited tests.
    def add_xml(self,extra):
        data=self.sesx.read_text(); data=data.replace('sourceOutPoint="72000"/>','sourceOutPoint="72000">'+extra+'</audioClip>')
        self.sesx.write_text(data); self.s=read_session(self.sesx); self.c=self.s.tracks[0][1][0]
    def test_fades_query_only(self):
        self.add_xml('<fadeIn startPoint="0" endPoint="9600" type="lin"/><fadeOut startPoint="38400" endPoint="48000" type="log"/>')
        self.assertIn('淡入',effect_summary(self.c)); self.assertIn('淡出',effect_summary(self.c))
        r=self.export(fade_policy='片段相对',sample_type='PCM 16')
        self.assertEqual(r['applied_effect_filters'],[])
        self.assertTrue(any('未应用' in w for w in r['warnings']))
        with wave.open(r['output'],'rb') as a,wave.open(str(self.source),'rb') as b:
            b.setpos(24000); self.assertEqual(a.readframes(48000),b.readframes(48000))
    def test_zero_length_fade_not_flagged(self):
        self.add_xml('<fadeIn startPoint="0" endPoint="0" type="lin"/>')
        self.assertNotIn('片段：淡入',effect_summary(self.c))
    def test_unknown_effect_query_and_baked(self):
        self.add_xml('<effect name="降噪" enabled="true"><parameter name="amount" parameterValue="0.5"/></effect>')
        self.assertIn('降噪',effect_summary(self.c))
        r=self.export(timeline_source=str(self.source))
        self.assertEqual(r['applied_effect_filters'],[]); self.assertEqual(r['source_begin'],2)
        r=self.export(); self.assertTrue(any('降噪' in w for w in r['warnings']))
    def test_inherited_processing_and_bypass(self):
        self.add_xml('<effectsRack bypass="true"><effect name="Reverb"/></effectsRack>')
        data=self.sesx.read_text().replace('<trackParameters>','<effect name="Track EQ"/><trackParameters>',1)
        self.sesx.write_text(data); c=read_session(self.sesx).tracks[0][1][0]
        self.assertTrue(next(x for x in c.effects if x['name']=='Reverb')['disabled'])
        self.assertTrue(any(x['name']=='Track EQ' and x['scope'].startswith('音轨') for x in c.effects))
    def test_settings_atomic_corrupt_and_paths(self):
        path=self.root/'prefs/settings.json'; prefs=Preferences(path)
        self.assertTrue(prefs.write({'values':{'lufs':'-18'},'directories':{'source':str(self.root/'gone')},'projects':{'x':{'mapping':{'0':'abc'}}}}))
        new=Preferences(path); self.assertEqual(new.data['values']['lufs'],'-18'); self.assertEqual(new.initialdir('source'),str(self.root))
        path.write_text('{bad'); broken=Preferences(path); self.assertTrue(broken.error); self.assertEqual(broken.data,{})
        self.assertTrue(broken.write({'values':{}})); self.assertEqual(json.loads(path.read_text())['version'],1)

del Fixture
if __name__=='__main__': unittest.main(verbosity=2)
