"""Audition-like choices with explicit, inspectable FFmpeg implementations."""
import math,re,dataclasses
from pathlib import Path
MODES=['关闭','ITU-R BS.1770-3 响度','ATSC A/85 响度','EBU R128 响度','总计 RMS','总计 RMS（匹配至文件）','峰值幅度','实际峰值幅度','响度（旧版）','感知响度（旧版）','True Peak Ceiling']
ALIASES={'峰值':'峰值幅度','EBU R128 LUFS':'EBU R128 响度'}
LUFS_MODES=MODES[1:4]
LEGACY=MODES[8:10]
DEFAULTS={MODES[1]:(-23,-1),MODES[2]:(-24,-2),MODES[3]:(-23,-1)}
NOTES={
'关闭':'不做响度增益调整；声道、采样率和编码转换仍执行。峰值保护可独立启用。',
MODES[1]:'使用 FFmpeg 的 BS.1770 / EBU 门控综合响度测量：K 加权、声道加权与静音门控，再以目标减实测值施加整段增益。BS.1770 定义测量，不规定固定目标；本工具默认 -23 LUFS。与 ATSC / EBU 预设共用测量器，不承诺与 Adobe 的 BS.1770-3 实现逐位一致或认证符合该版本。',
MODES[2]:'ATSC A/85 兼容预设：默认 -24 LKFS（与 LUFS 数值同尺度）、-2 dBTP 保护值。本工具共用门控综合响度测量，并不自动识别对白锚点或写入 dialnorm。与 EBU 的主要区别为交付目标和工作规范；不是独立的神奇算法，也不构成广播合规认证。',
MODES[3]:'EBU R128 预设：默认 -23 LUFS、-1 dBTP，K 加权/声道加权/门控综合响度；比 RMS 更重视频谱及低电平段门控。先测量再施加固定增益，尽量保留淡变与动态。最终响度必须复测；峰值保护可能使结果低于目标。',
MODES[4]:'计算整段、全部输出声道采样值的均方根，20×log10(RMS)，0 dBFS 对应满幅恒定样本。静音与淡变参与平均，没有 K 加权和静音门控；与 LUFS 不可直接互换。默认 -20 dBFS。满幅正弦约 -3.01 dBFS RMS；其他软件可能采用不同的正弦参考校准。',
MODES[5]:'将参考文件按当前输出声道/采样率转换后，测量整个参考文件的 RMS，作为每个片段的 RMS 目标；参考中的静音同样计入。只匹配能量平均值，不复制音色、动态、效果或 LUFS。请指定完整参考文件。',
MODES[6]:'以最大采样值为基准施加固定增益至峰值目标 dBFS。只对齐最高采样点，平均听感响度可能不同；采样间真实峰值也可能更高。',
MODES[7]:'测量采样间真实峰值，施加固定增益，使峰值接近目标 dBTP。与采样峰值不同，会考虑重建波形；与 Ceiling 不同，低电平片段也会提升。最终编码后复测，测量精度与安全余量可能使结果稍低于目标。',
MODES[8]:'Adobe 旧版响度匹配的精确算法/校准没有在本工具中实现。此项仅显示说明，导出和预览会明确拒绝执行；请在 Audition 匹配后缩混，不用 RMS 或 LUFS 冒充。',
MODES[9]:'Adobe 旧版感知响度算法没有可靠的等价实现。此项仅显示说明，导出和预览会拒绝执行；请在 Audition 匹配后缩混。本工具的 K 加权 LUFS 模式不是该旧版算法。',
'True Peak Ceiling':'只在实测真实峰值超过上限时衰减，不提升低电平片段，不匹配平均响度。默认整段衰减以保留波形；可选择过采样限制器，但不会复现 Adobe 限制器。'}
def note(mode):
    return NOTES.get(ALIASES.get(mode,mode),'未知模式')+'\n自动保护仅在 LUFS / Ceiling 模式启用；实际峰值匹配始终校验目标上限。整段衰减保留动态但可能无法达到目标；过采样限制器改变瞬态以争取目标，预读/释放仅对此方式生效。最终测量超出容差会记入日志，容差不是广播标准认证阈值。'

def rms(path,cancel=None):
    from core import run,executable,ExportError
    _,err=run([executable('ffmpeg'),'-hide_banner','-nostdin','-i',path,'-map','0:a:0','-af','astats=reset=0','-f','null','-'],cancel)
    section=err.rsplit('Overall',1)[-1]
    values=re.findall(r'RMS level dB: ([-+\w.]+)',section)
    if not values: raise ExportError('无法测量整段 RMS')
    return float(values[-1])

def channel_filters(option,info):
    from core import ExportError
    if option in ('左声道','右声道'):
        index=0 if option=='左声道' else 1
        if info['channels']<=index: raise ExportError('源音频没有右声道')
        return [f'pan=mono|c0=c{index}']
    if option=='单声道混合': return ['aformat=channel_layouts=mono']
    if option=='立体声': return ['aformat=channel_layouts=stereo']
    return []

def render(raw,result,tmp,opt,rate,codec,cancel=None):
    from core import ff,loudness,sample_peak,probe,ExportError
    import os
    mode=ALIASES.get(opt.mode,opt.mode); raw=Path(raw); tmp=Path(tmp)
    warnings=[]; report={'mode':mode,'implementation':'测量 + 整段固定增益 + 可选峰值保护','warnings':warnings}
    gain=0.; target=None; metric=None
    if mode in LUFS_MODES:
        stats=loudness(raw,opt,cancel); value=float(stats['input_i']); target=opt.lufs; metric='LUFS'; report['input_loudness']=stats
        if not math.isfinite(value): raise ExportError('静音或过短片段无法可靠测量 LUFS；请选择关闭或峰值模式。')
        gain=target-value
    elif mode in ('总计 RMS','总计 RMS（匹配至文件）'):
        value=rms(raw,cancel); target=opt.rms_target; metric='RMS dBFS'
        if mode.endswith('（匹配至文件）'):
            if not Path(opt.reference_file).is_file(): raise ExportError('请选择有效的 RMS 参考文件')
            info=probe(opt.reference_file,cancel); ref=tmp/'reference.wav'
            ff(opt.reference_file,ref,','.join(channel_filters(opt.channels,info)+[f'aresample={rate}']),cancel=cancel)
            target=rms(ref,cancel); report['reference_file']=opt.reference_file; report['reference_rms']=target
            if not math.isfinite(target): raise ExportError('RMS 参考文件为静音，无法作为匹配目标')
        if not math.isfinite(value): raise ExportError('片段为静音，无法匹配有限 RMS 目标')
        gain=target-value; report['input_rms']=value
    elif mode=='峰值幅度':
        value=sample_peak(raw,cancel); target=opt.peak; metric='sample peak dBFS'
        if math.isfinite(value): gain=target-value
        else: warnings.append('静音：保持静音，不提升峰值')
    elif mode=='实际峰值幅度':
        stats=loudness(raw,opt,cancel); value=float(stats['input_tp']); target=opt.peak; metric='true peak dBTP'
        if math.isfinite(value): gain=target-value-.05
        else: warnings.append('静音：保持静音，不提升峰值')
        report['input_loudness']=stats
    processed=raw
    if gain:
        processed=tmp/'gain.wav'; ff(raw,processed,f'volume={gain}dB',cancel=cancel)
    report.update(gain_db=gain,target=target,metric=metric)
    protection=opt.peak_protection
    if protection=='自动': protection='整段衰减' if mode in LUFS_MODES or mode=='True Peak Ceiling' else '关闭'
    ceiling=min(opt.ceiling,opt.peak) if mode=='实际峰值幅度' else opt.ceiling
    if mode in ('True Peak Ceiling','实际峰值幅度') and protection=='关闭': protection='整段衰减'
    report['protection']=protection
    if protection!='关闭':
        stats=loudness(processed,opt,cancel); tp=float(stats['input_tp'])
        if math.isfinite(tp) and tp>ceiling-.04:
            if protection=='过采样限制器' and ceiling < -23.9:
                protection='整段衰减'; report['protection']=protection
                warnings.append('该目标低于 FFmpeg 限制器支持范围，改用整段衰减保护。')
            protected=tmp/'protected.wav'
            if protection=='过采样限制器':
                limit=10**((ceiling-.1)/20)
                filt=f'aresample=192000,alimiter=limit={limit}:attack={opt.lookahead}:release={opt.release}:level=false:latency=true,aresample={rate}'
                warnings.append('已使用 FFmpeg 192 kHz 过采样限制器；改变瞬态，非 Adobe 等价算法。')
            else:
                filt=f'volume={ceiling-tp-.08}dB'; warnings.append('为满足真实峰值上限衰减整段；响度可能低于目标。')
            ff(processed,protected,filt,cancel=cancel); processed=protected
    elif sample_peak(processed,cancel)>0 and codec not in ('pcm_f32le','pcm_f64le'):
        raise ExportError('匹配增益将导致整数 PCM 削波；请启用峰值保护或降低目标。')
    ff(processed,result,codec=codec,cancel=cancel)
    if protection!='关闭':
        for attempt in range(4):
            stats=loudness(result,opt,cancel); tp=float(stats['input_tp'])
            if not math.isfinite(tp) or tp<=ceiling-.04: break
            reduced=tmp/f'reduced{attempt}.wav'
            ff(result,reduced,f'volume={ceiling-tp-.08}dB',codec,cancel); os.replace(reduced,result)
        else: raise ExportError('最终真实峰值未达到上限')
        report['final_loudness']=stats
    if mode in LUFS_MODES:
        stats=report.get('final_loudness') or loudness(result,opt,cancel)
        report['final_loudness']=stats; actual=float(stats['input_i'])
    elif metric=='RMS dBFS': actual=rms(result,cancel); report['final_rms']=actual
    elif metric=='sample peak dBFS': actual=sample_peak(result,cancel)
    elif metric=='true peak dBTP':
        stats=report.get('final_loudness') or loudness(result,opt,cancel); report['final_loudness']=stats; actual=float(stats['input_tp'])
    else: actual=None
    report['actual']=actual
    if actual is not None and target is not None:
        report['target_met']=math.isfinite(actual) and abs(actual-target)<=opt.tolerance
        if not report['target_met']: warnings.append(f'最终 {metric}={actual}，目标={target}，超出容差 ±{opt.tolerance}；已记录真实结果。')
    return report
