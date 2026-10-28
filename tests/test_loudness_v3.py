import sys,unittest,math,wave,struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_export import Tests as Fixture
from core import *
from loudness_modes import rms,note,MODES
class LoudnessV3(unittest.TestCase):
    setUp=Fixture.setUp; tearDown=Fixture.tearDown; export=Fixture.export
    def test_rms(self):
        r=self.export(mode='总计 RMS',rms_target=-20)
        self.assertAlmostEqual(rms(r['output']),-20,delta=.02)
        self.assertTrue(r['matching']['target_met'])
    def test_reference_rms(self):
        r=self.export(mode='总计 RMS（匹配至文件）',reference_file=str(self.source),channels='左声道')
        self.assertAlmostEqual(r['matching']['actual'],r['matching']['reference_rms'],delta=.02)
        with self.assertRaises(ExportError): self.export(mode='总计 RMS（匹配至文件）',reference_file='/missing')
    def test_true_peak_normalization(self):
        r=self.export(mode='实际峰值幅度',peak=-3,ceiling=-1)
        self.assertAlmostEqual(float(r['final_loudness']['input_tp']),-3,delta=.15)
    def test_presets(self):
        self.c.end=self.c.start+3; self.c.source_out=self.c.source_in+3
        for mode,target in [('ITU-R BS.1770-3 响度',-23),('ATSC A/85 响度',-24),('EBU R128 响度',-23)]:
            r=self.export(mode=mode,lufs=target)
            self.assertAlmostEqual(r['matching']['actual'],target,delta=.15)
    def test_limiter_final_measurement(self):
        r=self.export(mode='总计 RMS',rms_target=-5,peak_protection='过采样限制器',ceiling=-1)
        self.assertLessEqual(float(r['final_loudness']['input_tp']),-1)
        self.assertEqual(r['output_properties']['duration'],1)
    def test_clip_rejected_and_attenuation(self):
        with self.assertRaises(ExportError): self.export(mode='总计 RMS',rms_target=-1,peak_protection='关闭')
        r=self.export(mode='总计 RMS',rms_target=-1,peak_protection='整段衰减')
        self.assertFalse(r['matching']['target_met'])
    def test_legacy_honest_and_notes(self):
        for mode in ('响度（旧版）','感知响度（旧版）'):
            with self.assertRaises(ExportError): self.export(mode=mode)
        self.assertTrue(all(len(note(mode))>50 for mode in MODES))
    def test_invalid_controls(self):
        for kw in ({'lookahead':0},{'release':0},{'tolerance':-1},{'rms_target':float('nan')}):
            with self.assertRaises(ExportError): Options(**kw).validate()
del Fixture
