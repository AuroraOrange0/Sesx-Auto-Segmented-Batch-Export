"""Read-only SESX extraction; never renders the Audition processing graph."""
from __future__ import annotations
import dataclasses, json, math, os, re, shutil, subprocess, tempfile, threading, unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET

class ExportError(Exception): pass
class Cancelled(ExportError): pass

@dataclasses.dataclass
class Clip:
    name: str
    file_id: str
    start: float
    end: float
    source_in: float
    source_out: float
    issue: str = ''
    effects: list = dataclasses.field(default_factory=list)
    time_scale: float = 1
    @property
    def duration(self): return self.end-self.start

@dataclasses.dataclass
class Session:
    path: Path
    rate: int
    attributes: dict
    files: dict
    tracks: list
    warnings: list

def read_session(path, unit='samples'):
    path = Path(path).resolve()
    if path.stat().st_size > 50_000_000: raise ExportError('SESX 超过 50 MB，拒绝读取。')
    data = path.read_bytes()
    if b'<!ENTITY' in data.upper(): raise ExportError('不支持带实体声明的 XML。')
    root = ET.fromstring(data)
    for el in root.iter(): el.tag = el.tag.split('}')[-1]
    session = root if root.tag == 'session' else root.find('.//session')
    if session is None: raise ExportError('没有找到 session。')
    try: rate = int(session.attrib['sampleRate'])
    except (KeyError, ValueError): raise ExportError('会话 sampleRate 缺失或无效，不能猜测时间单位。')
    if not 8000 <= rate <= 384000: raise ExportError('会话采样率不在支持范围内。')
    if unit not in ('samples','seconds'): raise ExportError('未知时间单位。')
    scale = rate if unit == 'samples' else 1
    files = {}
    for f in root.iter('file'):
        if 'id' not in f.attrib: continue
        candidates = [f.get('relativePath',''), f.get('absolutePath',''), f.get('path','')]
        resolved = ''
        for candidate in candidates:
            if not candidate: continue
            p = Path(candidate.replace('\\','/')).expanduser()
            if not p.is_absolute(): p = path.parent / p
            if p.is_file(): resolved = str(p.resolve()); break
        files[f.get('id')] = {'attributes':dict(f.attrib), 'original':next((x for x in candidates if x),''), 'path':resolved}
    tracks, warnings = [], ['源截取模式不会应用 Audition 效果、淡入淡出、增益/声像包络、静音或声道映射；源文件声道为默认依据。']
    from effects import inspect_scope
    global_effects=inspect_scope(session,'会话/总线（候选，不推断路由）',skip=('audioTrack','files'))
    for i, track in enumerate(session.iter('audioTrack')):
        name = track.findtext('trackParameters/name') or track.get('name') or f'音轨 {i+1}'
        clips=[]
        track_effects=inspect_scope(track,f'音轨 {i+1}: {name}',skip=('audioClip',))
        for n, el in enumerate(track.iter('audioClip')):
            try:
                def t(key):
                    v=float(el.attrib[key])/scale
                    if not math.isfinite(v): raise ValueError('非有限时间')
                    return v
                start,end,si=t('startPoint'),t('endPoint'),t('sourceInPoint')
                so=t('sourceOutPoint') if 'sourceOutPoint' in el.attrib else si+end-start
                issue=''
                if min(start,si)<0 or end<=start or so<=si: issue='边界无效'
                if abs((so-si)-(end-start))>1.5/rate: issue='源与时间轴时长不一致：可能包含伸缩/循环，拒绝直接截取'
                for key in ('stretchRatio','playbackRate','speed'):
                    if key in el.attrib and abs(float(el.get(key))-1)>1e-7: issue='不支持时间伸缩'
                clip=Clip(el.get('name') or f'未命名_{n+1}',el.attrib['fileID'],start,end,si,so,issue)
                clip.effects=inspect_scope(el,'片段')+track_effects+global_effects
                clip.time_scale=scale
                clips.append(clip)
            except (ValueError,KeyError) as e:
                raise ExportError(f'{name} 第 {n+1} 个片段字段无效：{e}')
        tracks.append((name,sorted(clips,key=lambda c:c.start)))
    if not tracks: raise ExportError('没有找到 audioTrack。')
    return Session(path,rate,dict(session.attrib),files,tracks,warnings)

def executable(name):
    found=shutil.which(name)
    if found: return found
    for prefix in ('/opt/homebrew/bin','/usr/local/bin'):
        p=Path(prefix)/name
        if p.is_file(): return str(p)
    raise ExportError(f'找不到 {name}。请安装 FFmpeg，参见使用说明。')

def run(args, cancel=None):
    if cancel and cancel.is_set(): raise Cancelled('已取消')
    p=subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while True:
            try: out,err=p.communicate(timeout=.2); break
            except subprocess.TimeoutExpired:
                if cancel and cancel.is_set():
                    p.terminate()
                    try: p.communicate(timeout=2)
                    except subprocess.TimeoutExpired: p.kill(); p.communicate()
                    raise Cancelled('已取消')
        if p.returncode: raise ExportError(err[-4000:])
        return out,err
    finally:
        if p.poll() is None: p.kill(); p.communicate()

def probe(path,cancel=None):
    out,_=run([executable('ffprobe'),'-v','error','-select_streams','a:0','-show_streams','-show_format','-of','json',path],cancel)
    obj=json.loads(out)
    if not obj.get('streams'): raise ExportError('没有音频流')
    s=obj['streams'][0]
    return dict(rate=int(s['sample_rate']),channels=int(s['channels']),codec=s['codec_name'],
                sample_fmt=s.get('sample_fmt','未知'),bits=int(s.get('bits_per_raw_sample') or s.get('bits_per_sample') or 0),
                duration=float(s.get('duration') or obj['format'].get('duration') or 0),bit_rate=s.get('bit_rate') or obj['format'].get('bit_rate','未知'))

CODECS={'PCM 16':'pcm_s16le','PCM 24':'pcm_s24le','PCM 32':'pcm_s32le','Float 32':'pcm_f32le','Float 64':'pcm_f64le'}
def auto_type(info):
    for label,codec in CODECS.items():
        if info['codec']==codec: return label
    if info['codec'].startswith('pcm_f'): return 'Float 32'
    if info['bits']>24: return 'PCM 32'
    if info['bits']>16: return 'PCM 24'
    return 'PCM 16'

@dataclasses.dataclass
class Options:
    rate: str='会话'
    channels: str='源文件'
    sample_type: str='源文件'
    mode: str='关闭'
    peak: float=-1
    lufs: float=-23
    ceiling: float=-1
    duplicates: str='自动编号'
    timeline_source: str=''
    fade_policy: str='关闭'
    def validate(self):
        if self.fade_policy not in ('关闭','片段相对','源相对','时间轴'): raise ExportError('未知淡变坐标基准')
        if self.rate not in ('会话','源文件') and not 8000<=int(self.rate)<=384000: raise ExportError('采样率须为 8000–384000。')
        if self.channels not in ('源文件','单声道混合','左声道','右声道','立体声'): raise ExportError('未知声道配置')
        if self.sample_type not in ('源文件',*CODECS): raise ExportError('未知采样类型')
        if self.mode not in ('关闭','峰值','EBU R128 LUFS','True Peak Ceiling'): raise ExportError('未知响度模式')
        if not all(math.isfinite(v) for v in (self.peak,self.lufs,self.ceiling)): raise ExportError('目标值须为有限数字')
        if not -60<=self.peak<=0 or not -70<=self.lufs<=-5 or not -9<=self.ceiling<=0: raise ExportError('峰值 -60～0 dBFS；LUFS -70～-5；True Peak -9～0 dBTP。')
        if self.duplicates not in ('自动编号','跳过','报错'): raise ExportError('未知重名策略')

def safe_name(name):
    name=unicodedata.normalize('NFC',name)
    name=re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_',name).strip(' .')
    # Keep filename safely below the filesystem byte limit, including suffixes.
    while len(name.encode('utf-8'))>180: name=name[:-1]
    return name or '未命名'

def destination(folder,name,policy,used):
    base=safe_name(name); i=1
    while True:
        p=Path(folder)/(base+('' if i==1 else f'_{i:03d}')+'.wav')
        key=unicodedata.normalize('NFC',str(p)).casefold()
        existing={unicodedata.normalize('NFC',str(x)).casefold() for x in Path(folder).iterdir()}
        if key not in used and key not in existing:
            used.add(key); return p
        if policy=='跳过': return None
        if policy=='报错': raise ExportError(f'文件名重复：{p.name}')
        i+=1

def ff(input_path,output_path,filters='',codec='pcm_f32le',cancel=None):
    args=[executable('ffmpeg'),'-hide_banner','-nostdin','-v','info','-y','-i',input_path,'-map','0:a:0','-vn','-map_metadata','-1']
    if filters: args+=['-af',filters]
    args+=['-c:a',codec,output_path]
    return run(args,cancel)

def loudness(path,opt,cancel=None):
    filt=f'loudnorm=I={opt.lufs}:TP={opt.ceiling}:LRA=11:print_format=json'
    _,err=run([executable('ffmpeg'),'-hide_banner','-nostdin','-i',path,'-map','0:a:0','-af',filt,'-f','null','-'],cancel)
    blocks=re.findall(r'\{\s*"input_i".*?\}',err,re.S)
    if not blocks: raise ExportError('未能读取响度测量')
    return json.loads(blocks[-1])

def sample_peak(path,cancel=None):
    _,err=run([executable('ffmpeg'),'-hide_banner','-nostdin','-i',path,'-map','0:a:0','-af','astats=reset=0','-f','null','-'],cancel)
    vals=re.findall(r'Peak level dB: ([-+\w.]+)',err)
    if not vals: raise ExportError('无法测量采样峰值')
    return max(float(x) for x in vals)

def export_clip(session,clip,mapping,folder,opt,used,cancel=None):
    opt.validate()
    from effects import processing_plan
    effect_filters,effect_warnings=processing_plan(clip,opt)
    if clip.issue and not opt.timeline_source: raise ExportError(clip.issue)
    src=opt.timeline_source or mapping.get(clip.file_id) or session.files.get(clip.file_id,{}).get('path')
    if not src or not Path(src).is_file(): raise ExportError(f'音源缺失：fileID={clip.file_id}')
    info=probe(src,cancel)
    begin=clip.start if opt.timeline_source else clip.source_in
    length=clip.duration
    if begin<0 or length<=0 or begin+length>info['duration']+1/info['rate']: raise ExportError('裁剪范围超出源文件；请检查映射、单位及时间对齐。')
    rate=session.rate if opt.rate=='会话' else info['rate'] if opt.rate=='源文件' else int(opt.rate)
    kind=auto_type(info) if opt.sample_type=='源文件' else opt.sample_type
    target=destination(folder,clip.name,opt.duplicates,used)
    if target is None: return {'status':'skipped','name':clip.name}
    a=round(begin*info['rate']); b=round((begin+length)*info['rate'])
    filters=[f'atrim=start_sample={a}:end_sample={b}','asetpts=PTS-STARTPTS']
    ch=opt.channels
    if ch in ('左声道','右声道'):
        index=0 if ch=='左声道' else 1
        if info['channels']<=index: raise ExportError('源音频没有右声道')
        filters.append(f'pan=mono|c0=c{index}')
    elif ch=='单声道混合': filters.append('aformat=channel_layouts=mono')
    elif ch=='立体声': filters.append('aformat=channel_layouts=stereo')
    filters.extend(effect_filters)
    filters.append(f'aresample={rate}')
    with tempfile.TemporaryDirectory(prefix='.sesx-',dir=folder) as tmp:
        raw=Path(tmp)/'raw.wav'; processed=raw; result=Path(tmp)/'result.wav'
        ff(src,raw,','.join(filters),cancel=cancel)
        report={'name':clip.name,'source':str(src),'source_begin':begin,'duration':length,'source_properties':info,
                'output':str(target),'options':dataclasses.asdict(opt),'warnings':effect_warnings,'sesx_processing':clip.effects,'applied_effect_filters':effect_filters}
        if opt.mode=='峰值':
            peak=sample_peak(raw,cancel)
            if math.isfinite(peak):
                processed=Path(tmp)/'peak.wav'; ff(raw,processed,f'volume={opt.peak-peak}dB',cancel=cancel)
            else: report['warnings'].append('静音：保持静音，不做峰值提升')
        elif opt.mode=='EBU R128 LUFS':
            stats=loudness(raw,opt,cancel); report['loudnorm_input']=stats
            if not all(math.isfinite(float(stats[k])) for k in ('input_i','input_tp','input_lra','input_thresh','target_offset')):
                raise ExportError('静音/过短音频无法可靠测量 LUFS；请改用关闭或峰值模式。')
            filt=f'loudnorm=I={opt.lufs}:TP={opt.ceiling}:LRA=11:linear=true:print_format=json'
            for key,param in [('input_i','measured_I'),('input_tp','measured_TP'),('input_lra','measured_LRA'),('input_thresh','measured_thresh'),('target_offset','offset')]: filt+=f':{param}={stats[key]}'
            processed=Path(tmp)/'lufs.wav'
            _,err=ff(raw,processed,filt+f',aresample={rate}',cancel=cancel)
            blocks=re.findall(r'\{\s*"input_i".*?\}',err,re.S)
            if blocks: report['loudnorm_render']=json.loads(blocks[-1])
            report['warnings'].append('峰值/动态范围约束可能导致动态处理或最终 LUFS 偏离目标；以最终测量为准。')
        ff(processed,result,codec=CODECS[kind],cancel=cancel)
        if opt.mode in ('True Peak Ceiling','EBU R128 LUFS'):
            # Measure the final encoded, resampled file. Attenuate the entire clip;
            # this is a ceiling-only gain operation, not a brickwall limiter.
            for attempt in range(4):
                stats=loudness(result,opt,cancel); tp=float(stats['input_tp'])
                if not math.isfinite(tp) or tp<=opt.ceiling-.04: break
                reduced=Path(tmp)/f'reduced{attempt}.wav'
                ff(result,reduced,f'volume={opt.ceiling-tp-.08}dB',CODECS[kind],cancel)
                os.replace(reduced,result)
            else: raise ExportError('最终真实峰值未达到上限，未提交文件')
            report['final_loudness']=stats
        output_info=probe(result,cancel)
        if abs(output_info['duration']-length)>max(2/rate,2/info['rate']): raise ExportError('输出时长校验失败')
        if cancel and cancel.is_set(): raise Cancelled('已取消')
        # Atomic no-clobber commit: never overwrites existing audio.
        os.link(result,target)
        report.update(status='ok',output_properties=output_info)
        return report
