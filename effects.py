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
    if record['tag'] in ('fadeIn','fadeOut'):
        try:
            if float(record['attributes']['startPoint'])==float(record['attributes']['endPoint']): return True
        except (KeyError,ValueError,TypeError): pass
    if record['has_keyframes']: return False
    cid=record['attributes'].get('componentID','')
    known={'Audition.Fader':('volume',1),'Audition.StereoPanner':('Pan',0),'Audition.Mute':('mute',0)}
    if cid not in known: return False
    name,value=known[cid]
    vals=[p.get('parameterValue') for p in record['parameters'] if p.get('name')==name]
    try: return bool(vals) and all(float(v)==value for v in vals)
    except (ValueError,TypeError): return False

def effect_summary(clip):
    records=clip.effects
    relevant=[r for r in records if not neutral(r)]
    direct=[r for r in relevant if r['scope']=='片段']
    inherited=[r for r in relevant if r['scope'].startswith('音轨')]
    buses=[r for r in relevant if r not in direct and r not in inherited]
    parts=[]
    for prefix,items in [('片段',direct),('音轨',inherited),('会话候选',buses)]:
        if items:
            names=[{'fadeIn':'淡入','fadeOut':'淡出'}.get(r['tag'],r['name']) for r in items]
            parts.append(prefix+'：'+'、'.join(dict.fromkeys(names)))
    if parts: return '；'.join(parts)
    if records and all(neutral(r) for r in records): return '仅发现旁路/中性处理（需核对）'
    return '未发现可识别效果（非无效果保证）'

def processing_plan(clip,opt):
    if opt.timeline_source:
        return [], ['已使用 Audition 烘焙缩混；按时间轴切段，不重复应用 SESX 效果。']
    relevant=[r for r in clip.effects if not neutral(r)]
    warnings=[]
    if relevant: warnings.append('原始音源截取未应用 SESX 效果：'+effect_summary(clip)+'。请在 Audition 手动导出，或选择整轨缩混。')
    if opt.fade_policy!='关闭': warnings.append('旧版淡变配置已忽略；v0.3 只查询效果，不直接渲染淡变。')
    return [],warnings
