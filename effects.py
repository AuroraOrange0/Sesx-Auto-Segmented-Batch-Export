"""Inspect serialized SESX processing; no proprietary plugin execution."""
import math
import xml.etree.ElementTree as ET

def inspect_scope(node, scope, skip=()):
    records=[]
    def visit(el, inherited_disabled=False):
        if el.tag in skip: return
        attrs=dict(el.attrib)
        disabled=inherited_disabled or any(attrs.get(k,'').lower() in ('true','1') for k in ('bypass','bypassed','isBypassed')) or attrs.get('enabled','').lower() in ('false','0')
        matched=el.tag in ('component','effect','plugin','vstPlugin','audioEffect','fadeIn','fadeOut')
        if matched:
            records.append({'scope':scope,'tag':el.tag,'name':attrs.get('name') or attrs.get('componentID') or el.tag,
                            'attributes':attrs,'disabled':disabled,'xml':ET.tostring(el,encoding='unicode'),
                            'parameters':[dict(p.attrib) for p in el.iter('parameter')],
                            'has_keyframes':any('keyframe' in p.tag.lower() for p in el.iter())})
            return
        for child in el: visit(child,disabled)
    visit(node)
    return records

def neutral(record):
    if record['disabled']: return True
    if record['has_keyframes']: return False
    cid=record['attributes'].get('componentID','')
    known={'Audition.Fader':('volume',1),'Audition.StereoPanner':('Pan',0),'Audition.Mute':('mute',0)}
    if cid not in known: return False
    name,value=known[cid]
    vals=[p.get('parameterValue') for p in record['parameters'] if p.get('name')==name]
    try: return bool(vals) and all(float(v)==value for v in vals)
    except (ValueError,TypeError): return False

def fade_filters(clip,policy):
    from core import ExportError
    if policy=='关闭': return []
    origin={'片段相对':0,'源相对':clip.source_in,'时间轴':clip.start}[policy]
    result=[]
    for rec in clip.effects:
        if rec['tag'] not in ('fadeIn','fadeOut') or rec['disabled']: continue
        attrs=rec['attributes']
        # Only implement a verified linear curve, never guess proprietary curves.
        if attrs.get('type','').lower() not in ('lin','linear'):
            raise ExportError('SESX 淡变曲线不是已支持的 linear；请先烘焙。')
        if rec['has_keyframes'] or any(tag in rec['xml'] for tag in ('controlPoint','curvePoint')):
            raise ExportError('不支持自定义淡变曲线；请先烘焙。')
        try: start=float(attrs['startPoint'])/clip.time_scale-origin; end=float(attrs['endPoint'])/clip.time_scale-origin
        except (ValueError,KeyError): raise ExportError('淡变边界无法解析')
        if not all(math.isfinite(x) for x in (start,end)) or start< -1e-8 or end>clip.duration+1e-8 or end<=start:
            raise ExportError('淡变边界与选择的坐标基准不符；请选择正确基准或烘焙。')
        result.append(f'afade=t={"in" if rec["tag"]=="fadeIn" else "out"}:st={max(0,start)}:d={end-start}:curve=tri')
    return result

def processing_plan(clip,opt):
    from core import ExportError
    records=clip.effects
    if opt.timeline_source:
        return [], ['已使用烘焙混缩；SESX 效果不重复应用，烘焙内容由用户在 Audition 确认。']
    other=[r for r in records if r['tag'] not in ('fadeIn','fadeOut') and not neutral(r)]
    if opt.fade_policy!='关闭' and other:
        raise ExportError('发现无法渲染的效果/自动化/路由：'+', '.join(r['name'] for r in other[:6])+'。请使用已烘焙整轨混缩。')
    filters=fade_filters(clip,opt.fade_policy)
    warnings=[]
    if other: warnings.append('源截取未应用以下 SESX 效果/处理：'+', '.join(r['name'] for r in other))
    if opt.fade_policy=='关闭' and any(r['tag'] in ('fadeIn','fadeOut') and not r['disabled'] for r in records): warnings.append('源截取未应用 SESX 淡入淡出。')
    if filters: warnings.append('已应用 SESX 线性淡变；坐标基准由用户选择，尚未对原工程验证。')
    return filters,warnings
