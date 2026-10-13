import sys, tempfile, unittest, math, wave, struct, threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import *

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.out=self.root/'out'; self.out.mkdir()
        self.source=self.root/'中文 source.wav'
        with wave.open(str(self.source),'wb') as f:
            f.setnchannels(2); f.setsampwidth(2); f.setframerate(48000)
            f.writeframes(b''.join(struct.pack('<hh',round(12000*math.sin(2*math.pi*440*i/48000)),round(5000*math.sin(2*math.pi*880*i/48000))) for i in range(48000*4)))
        self.sesx=self.root/'test.sesx'
        self.sesx.write_text('''<sesx><session sampleRate="48000" bitDepth="32"><files><file id="0" relativePath="中文 source.wav"/></files><tracks><audioTrack id="1"><trackParameters><name>有效稿</name></trackParameters><audioClip name="预报/中文" fileID="0" startPoint="96000" endPoint="144000" sourceInPoint="24000" sourceOutPoint="72000"/><audioClip name="预报/中文" fileID="0" startPoint="144000" endPoint="192000" sourceInPoint="72000" sourceOutPoint="120000"/></audioTrack><audioTrack id="2"><audioClip name="废稿" fileID="0" startPoint="0" endPoint="24000" sourceInPoint="0"/></audioTrack></tracks></session></sesx>''',encoding='utf-8')
        self.s=read_session(self.sesx); self.c=self.s.tracks[0][1][0]
    def tearDown(self): self.tmp.cleanup()
    def export(self,**kw): return export_clip(self.s,self.c,{},self.out,Options(**kw),set())
    def test_parse_and_exact_audio(self):
        self.assertEqual(self.s.tracks[0][0],'有效稿'); self.assertEqual(self.c.source_in,.5)
        r=self.export(); self.assertEqual(r['output_properties']['codec'],'pcm_s16le')
        with wave.open(r['output'],'rb') as a,wave.open(str(self.source),'rb') as b:
            b.setpos(24000); self.assertEqual(a.readframes(48000),b.readframes(48000))
    def test_encoding_rate_channels(self):
        for kind,codec in CODECS.items():
            r=self.export(rate='44100',channels='左声道',sample_type=kind)
            self.assertEqual(r['output_properties']['codec'],codec)
            self.assertEqual(r['output_properties']['channels'],1)
            self.assertEqual(r['output_properties']['rate'],44100)
            self.assertAlmostEqual(r['output_properties']['duration'],1,places=4)
    def test_peak(self):
        r=self.export(mode='峰值',peak=-3,sample_type='Float 32')
        self.assertAlmostEqual(sample_peak(r['output']),-3,places=3)
    def test_lufs(self):
        self.c.end=self.c.start+3; self.c.source_out=self.c.source_in+3
        r=self.export(mode='EBU R128 LUFS',lufs=-23,sample_type='PCM 24')
        stats=r['final_loudness']; self.assertLessEqual(float(stats['input_tp']),-1)
        self.assertAlmostEqual(float(stats['input_i']),-23,delta=.3)
    def test_ceiling(self):
        r=self.export(mode='True Peak Ceiling',ceiling=-9)
        self.assertLessEqual(float(r['final_loudness']['input_tp']),-9)
    def test_ceiling_does_not_boost(self):
        r=self.export(mode='True Peak Ceiling',ceiling=-1)
        self.assertAlmostEqual(sample_peak(r['output']),sample_peak(self.source),delta=.01)
    def test_names_no_overwrite(self):
        used=set(); a=destination(self.out,'../a','自动编号',used); a.write_bytes(b'keep')
        b=destination(self.out,'../a','自动编号',used); self.assertNotEqual(a,b)
        self.assertEqual(a.read_bytes(),b'keep'); self.assertIsNone(destination(self.out,'../a','跳过',set()))
        with self.assertRaises(ExportError): destination(self.out,'../a','报错',set())
        self.assertEqual(a.parent,self.out)
    def test_mapping_and_timeline(self):
        self.c.source_in=100; self.c.source_out=101
        with self.assertRaises(ExportError): self.export()
        r=self.export(timeline_source=str(self.source)); self.assertEqual(r['source_begin'],2)
    def test_missing_stretch_cancel(self):
        with self.assertRaises(ExportError): export_clip(self.s,self.c,{'0':'/missing'},self.out,Options(),set())
        self.c.issue='伸缩';
        with self.assertRaises(ExportError): self.export()
        self.c.issue=''; event=threading.Event(); event.set()
        with self.assertRaises(Cancelled): export_clip(self.s,self.c,{},self.out,Options(),set(),event)
    def test_silence_lufs_and_peak(self):
        silence=self.root/'silence.wav'
        with wave.open(str(silence),'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000); f.writeframes(bytes(48000*4*2))
        with self.assertRaises(ExportError): export_clip(self.s,self.c,{'0':str(silence)},self.out,Options(mode='EBU R128 LUFS'),set())
        r=export_clip(self.s,self.c,{'0':str(silence)},self.out,Options(mode='峰值'),set())
        self.assertTrue(r['warnings'])
    def test_seconds_and_namespace(self):
        self.sesx.write_text('<sesx xmlns="test"><session sampleRate="48000"><audioTrack><audioClip name="x" fileID="0" startPoint="0" endPoint="1.5" sourceInPoint="0.25" sourceOutPoint="1.75"/></audioTrack></session></sesx>')
        s=read_session(self.sesx,'seconds'); self.assertEqual(s.tracks[0][1][0].duration,1.5)
    def test_invalid_options(self):
        for kw in ({'lufs':float('nan')},{'rate':'0'},{'sample_type':'bad'},{'ceiling':-20}):
            with self.assertRaises(ExportError): Options(**kw).validate()

if __name__=='__main__': unittest.main(verbosity=2)
