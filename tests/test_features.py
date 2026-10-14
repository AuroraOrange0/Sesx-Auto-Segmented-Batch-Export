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
    def test_fades_render(self):
        self.add_xml('<fadeIn startPoint="0" endPoint="9600" type="lin"/><fadeOut startPoint="38400" endPoint="48000" type="lin"/>')
        r=self.export(fade_policy='片段相对',sample_type='PCM 16')
        self.assertEqual(len(r['applied_effect_filters']),2)
        with wave.open(r['output'],'rb') as f: data=f.readframes(48000)
        samples=struct.unpack('<'+str(len(data)//2)+'h',data)
        self.assertLess(abs(samples[200]),500)
        self.assertGreater(max(abs(x) for x in samples[30000:35000]),11000)
        self.assertLess(abs(samples[-100]),500)
    def test_unknown_effect_blocked_and_baked(self):
        self.add_xml('<effect name="降噪" enabled="true"><parameter name="amount" parameterValue="0.5"/></effect>')
        self.assertEqual(self.c.effects[0]['name'],'降噪')
        with self.assertRaises(ExportError): self.export(fade_policy='片段相对')
        r=self.export(timeline_source=str(self.source),fade_policy='片段相对')
        self.assertEqual(r['applied_effect_filters'],[])
        r=self.export(); self.assertTrue(any('降噪' in w for w in r['warnings']))
    def test_curve_and_coordinate_rejection(self):
        self.add_xml('<fadeIn startPoint="24000" endPoint="33600" type="log"/>')
        with self.assertRaises(ExportError): self.export(fade_policy='源相对')
        self.c.effects[0]['attributes']['type']='lin'
        self.assertIn('st=0',fade_filters(self.c,'源相对')[0])
        self.c.effects[0]['attributes']['startPoint']='-1'
        with self.assertRaises(ExportError): fade_filters(self.c,'片段相对')
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
