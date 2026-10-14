#!/usr/bin/env python3
import csv, dataclasses, datetime, json, queue, subprocess, sys, threading, traceback
from pathlib import Path
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    print(f'当前 Python {sys.version_info.major}.{sys.version_info.minor} 缺少 Tkinter。Homebrew 用户请安装：brew install python-tk@{sys.version_info.major}.{sys.version_info.minor}，然后配置 PyCharm 使用同版本解释器。')
    raise SystemExit(1)
from core import *
from preferences import Preferences

class App:
    def __init__(self,root,settings_path=None):
        self.root=root; root.title('SESX 片段批量导出 v0.2'); root.geometry('1280x960'); root.minsize(1100,900)
        self.session=None; self.mapping={}; self.q=queue.Queue(); self.cancel=threading.Event(); self.busy=False
        self.vars={}; self.mutators=[]
        self.preferences=Preferences(settings_path); self.save_job=None; self.restoring=True
        self.projects=self.preferences.data.get("projects",{})
        if not isinstance(self.projects,dict): self.projects={}
        self.directories=self.preferences.data.get("directories",{})
        if not isinstance(self.directories,dict): self.directories={} 
        top=ttk.Frame(root,padding=10); top.pack(fill='x')
        self.button(top,'选择 SESX',self.load).pack(side='left')
        self.pathlabel=ttk.Label(top,text='尚未选择工程'); self.pathlabel.pack(side='left',padx=10)
        ttk.Label(top,text='时间字段单位').pack(side='left')
        self.unit=self.combo(top,'unit',['samples','seconds'],'samples',12); self.unit.pack(side='left')
        self.unit.bind('<<ComboboxSelected>>',lambda e:self.reload())
        self.info=tk.Text(root,height=4,wrap='word'); self.info.pack(fill='x',padx=10)
        self.info.insert('end','只读解析 SESX；直接截取不会渲染 Audition 实时效果、淡入淡出、包络、声像或轨道处理。\n默认时间单位为会话采样点。首次使用请核对预览与 Audition 边界。')
        row=ttk.Frame(root,padding=10); row.pack(fill='x')
        ttk.Label(row,text='音轨').pack(side='left')
        self.track=self.combo(row,'track',[],'',28); self.track.pack(side='left',padx=5)
        self.track.bind('<<ComboboxSelected>>',lambda e:self.change_track())
        self.button(row,'映射所选片段音源',self.map_source).pack(side='left',padx=5)
        self.button(row,'查看源文件映射',self.show_mapping).pack(side='left')
        self.button(row,'试听所选原始片段',self.listen).pack(side='left',padx=5)
        effectsrow=ttk.Frame(root,padding=(10,0)); effectsrow.pack(fill='x')
        self.button(effectsrow,'查询所选片段效果 / 处理链',self.show_effects).pack(side='left')
        self.button(effectsrow,'预览当前导出效果',self.preview_output).pack(side='left',padx=10)
        ttk.Label(effectsrow,text='预览与导出共用同一处理流程；降噪/混响等需先烘焙。').pack(side='left')
        tableframe=ttk.Frame(root); tableframe.pack(fill='both',expand=True,padx=10)
        columns=('name','start','source','duration','file','status')
        self.table=ttk.Treeview(tableframe,columns=columns,show='headings',selectmode='extended')
        for c,title,width in zip(columns,('片段名称','时间轴起点 (秒)','源偏移 (秒)','时长 (秒)','fileID / 文件','状态'),(270,120,120,100,260,210)):
            self.table.heading(c,text=title); self.table.column(c,width=width)
        scroll=ttk.Scrollbar(tableframe,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y'); self.table.pack(fill='both',expand=True)
        config=ttk.LabelFrame(root,text='导出设置（WAV；默认会话采样率 + 每个源文件的声道/编码）',padding=8); config.pack(fill='x',padx=10,pady=5)
        fields=[('rate','采样率',['会话','源文件','44100','48000','96000'],'会话'),('channels','声道',['源文件','单声道混合','左声道','右声道','立体声'],'源文件'),('sample_type','采样类型',['源文件',*CODECS],'源文件'),('mode','响度模式',['关闭','峰值','EBU R128 LUFS','True Peak Ceiling'],'关闭'),('duplicates','重名处理',['自动编号','跳过','报错'],'自动编号')]
        for col,(key,label,values,default) in enumerate(fields):
            ttk.Label(config,text=label).grid(row=0,column=col,sticky='w',padx=5)
            self.combo(config,key,values,default,22,editable=key=='rate').grid(row=1,column=col,padx=5,sticky='ew')
        for col,(key,label,value) in enumerate([('peak','峰值 dBFS','-1'),('lufs','目标 LUFS','-23'),('ceiling','真实峰值上限 dBTP','-1')]):
            frame=ttk.Frame(config); frame.grid(row=2,column=col,pady=5,sticky='w')
            ttk.Label(frame,text=label).pack(side='left'); self.vars[key]=tk.StringVar(value=value)
            entry=ttk.Entry(frame,textvariable=self.vars[key],width=7); entry.pack(side='left'); self.mutators.append(entry)
        ttk.Label(config,text='Ceiling：整段衰减，不提升音量。LUFS：两遍匹配 + 最终文件峰值校验。').grid(row=3,column=0,columnspan=5,sticky='w')
        fades=ttk.Frame(root,padding=(10,5)); fades.pack(fill='x')
        ttk.Label(fades,text='SESX 线性淡入淡出（实验，坐标基准需核对）').pack(side='left')
        self.combo(fades,'fade_policy',['关闭','片段相对','源相对','时间轴'],'关闭',16).pack(side='left',padx=10)
        ttk.Label(fades,text='发现无法渲染的处理时拒绝仅应用淡变；烘焙模式不重复处理。').pack(side='left')
        out=ttk.Frame(root,padding=10); out.pack(fill='x')
        self.vars['output']=tk.StringVar(); ttk.Label(out,text='输出目录').pack(side='left')
        entry=ttk.Entry(out,textvariable=self.vars['output']); entry.pack(side='left',fill='x',expand=True,padx=5); self.mutators.append(entry)
        self.button(out,'选择目录',self.pick_output).pack(side='left')
        mix=ttk.Frame(root,padding=(10,0)); mix.pack(fill='x')
        self.vars['timeline_source']=tk.StringVar()
        self.button(mix,'选择已烘焙整轨混缩（可选）',self.pick_mix).pack(side='left')
        self.button(mix,'清除混缩',lambda:self.vars['timeline_source'].set('')).pack(side='left',padx=5)
        ttk.Label(mix,textvariable=self.vars['timeline_source']).pack(side='left')
        ttk.Label(root,text='整轨混缩模式按时间轴起止截取：混缩必须从会话 0 秒开始，使用所需音轨并已烘焙效果。').pack(anchor='w',padx=10)
        actions=ttk.Frame(root,padding=10); actions.pack(fill='x')
        self.button(actions,'导出全部片段',lambda:self.start(False)).pack(side='left')
        self.button(actions,'导出所选片段',lambda:self.start(True)).pack(side='left',padx=10)
        ttk.Button(actions,text='取消',command=self.cancel.set).pack(side='left')
        self.button(actions,'保存预览 CSV',self.save_csv).pack(side='left',padx=10)
        self.progress=ttk.Label(actions,text='就绪'); self.progress.pack(side='left')
        self.log=tk.Text(root,height=7,wrap='word'); self.log.pack(fill='x',padx=10,pady=(0,10))
        root.protocol('WM_DELETE_WINDOW',self.close); root.after(100,self.poll)
        self.play_tmp=None
        self.last_preview_report=None
        self.restore_globals()
        self.restoring=False
        for var in self.vars.values(): var.trace_add('write',self.schedule_save)
        if self.preferences.error: self.logline(self.preferences.error)
        last=self.preferences.data.get('last_session','')
        if isinstance(last,str) and last:
            if Path(last).is_file(): root.after(0,lambda:self.reload(last))
            else: self.logline('上次工程已不存在，请重新选择：'+last)
    def restore_globals(self):
        values=self.preferences.data.get('values',{})
        if not isinstance(values,dict): return
        for key,var in self.vars.items():
            if key in ('track','timeline_source'): continue
            value=values.get(key)
            if isinstance(value,str):
                widget=next((w for w in self.mutators if isinstance(w,ttk.Combobox) and str(w.cget('textvariable'))==str(var)),None)
                if widget and getattr(widget,'_idle_state','')=='readonly' and value not in widget.cget('values'): continue
                var.set(value)
    def schedule_save(self,*args):
        if self.restoring: return
        if self.save_job: self.root.after_cancel(self.save_job)
        self.save_job=self.root.after(350,self.save_settings)
    def save_settings(self):
        if self.save_job:
            self.root.after_cancel(self.save_job); self.save_job=None
        values={k:v.get() for k,v in self.vars.items() if k not in ('track','timeline_source')}
        if self.session:
            self.projects[str(self.session.path)]={'mapping':dict(self.mapping),'track':self.vars['track'].get(),'timeline_source':self.vars['timeline_source'].get()}
        data={'values':values,'directories':self.directories,'projects':self.projects,
              'last_session':str(self.session.path) if self.session else self.preferences.data.get('last_session','')}
        if not self.preferences.write(data): self.logline(self.preferences.error)
    def dialog_dir(self,kind):
        self.preferences.data['directories']=self.directories
        return self.preferences.initialdir(kind)
    def remember_path(self,kind,path,directory=False):
        self.directories[kind]=str(Path(path).resolve() if directory else Path(path).resolve().parent)
        self.schedule_save()
    def preview_output(self):
        if self.busy or not self.session or not self.table.selection():
            self.logline('请先选择一个片段，再预览。'); return
        try: opt=self.options(); opt.validate()
        except Exception as e: messagebox.showerror('预览设置错误',str(e)); return
        clip=self.clips()[int(self.table.selection()[0])]
        session=self.session; mapping=dict(self.mapping)
        opt=dataclasses.replace(opt,duplicates='自动编号')
        self.cancel.clear(); self.lock(True); self.progress.configure(text='正在渲染预览…')
        def worker():
            try:
                if self.play_tmp: self.play_tmp.cleanup()
                self.play_tmp=tempfile.TemporaryDirectory(prefix='sesx-effect-preview-')
                report=export_clip(session,clip,mapping,Path(self.play_tmp.name),opt,set(),self.cancel)
                self.last_preview_report=report
                self.q.put(('log','预览已渲染：'+report['output']))
                for warning in report.get('warnings',[]): self.q.put(('log',warning))
                self.q.put(('log','预览输出属性：'+json.dumps(report['output_properties'],ensure_ascii=False)))
                if report.get('final_loudness'): self.q.put(('log','最终响度：'+json.dumps(report['final_loudness'],ensure_ascii=False)))
                player=shutil.which('afplay')
                if not player: raise ExportError('试听需要 macOS afplay')
                run([player,report['output']],self.cancel)
            except Exception as e: self.q.put(('log','效果预览失败/取消：'+str(e)))
            finally: self.q.put(('done','当前导出效果预览结束'))
        threading.Thread(target=worker,daemon=True).start()
    def show_effects(self):
        if not self.session or not self.table.selection():
            self.logline('请选择片段查询效果。'); return
        report=[]
        from effects import processing_plan
        for iid in self.table.selection():
            c=self.clips()[int(iid)]
            try:
                filters,warnings=processing_plan(c,self.options())
                state='当前可应用滤镜：'+str(filters)+'；'+ '；'.join(warnings)
            except Exception as e: state='当前处理不可执行：'+str(e)
            report.append({'clip':c.name,'state':state,'effects':c.effects})
        window=tk.Toplevel(self.root); window.title('SESX 效果查询（声明 ≠ 已渲染）'); window.geometry('1000x650')
        ttk.Label(window,text='片段 / 音轨 / 会话候选处理；总线未推断路由，已旁路信息也保留。烘焙源的实际处理不能从 SESX 证明。').pack(fill='x')
        text=tk.Text(window,wrap='word'); text.pack(fill='both',expand=True)
        for item in report:
            text.insert('end',item['clip']+'\n'+item['state']+'\n')
            if not item['effects']: text.insert('end','未发现可识别的处理声明；不代表工程没有效果。\n')
            for r in item['effects']:
                text.insert('end',f"[{r['scope']}] {r['name']} — {'已旁路/禁用' if r['disabled'] else '启用状态需核对'}\n{r['xml']}\n\n")
        text.configure(state='disabled')
        def save():
            path=filedialog.asksaveasfilename(parent=window,initialdir=self.dialog_dir('report'),defaultextension='.json')
            if path:
                try:
                    Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); self.remember_path('report',path)
                except OSError as e: messagebox.showerror('保存失败',str(e),parent=window)
        ttk.Button(window,text='保存效果查询 JSON',command=save).pack(pady=5)
    def button(self,parent,label,command):
        b=ttk.Button(parent,text=label,command=command); self.mutators.append(b); return b
    def combo(self,parent,key,values,default,width,editable=False):
        self.vars[key]=tk.StringVar(value=default)
        c=ttk.Combobox(parent,textvariable=self.vars[key],values=values,width=width,state='normal' if editable else 'readonly')
        c._idle_state='normal' if editable else 'readonly'; self.mutators.append(c); return c
    def lock(self,value):
        self.busy=value
        for w in self.mutators: w.configure(state='disabled' if value else getattr(w,'_idle_state','normal'))
    def logline(self,text): self.log.insert('end',str(text)+'\n'); self.log.see('end')
    def load(self):
        p=filedialog.askopenfilename(initialdir=self.dialog_dir('sesx'),filetypes=[('Audition SESX','*.sesx')])
        if p: self.remember_path("sesx",p); self.reload(p)
    def reload(self,p=None):
        if p is None and self.session: p=self.session.path
        if not p: return
        try:
            session=read_session(p,self.vars['unit'].get())
            if self.session: self.save_settings()
            self.restoring=True
            self.session=session; self.mapping={k:v['path'] for k,v in session.files.items()}
            saved=self.projects.get(str(session.path),{})
            if not isinstance(saved,dict): saved={}
            saved_mapping=saved.get('mapping',{})
            if isinstance(saved_mapping,dict):
                for fid,path in saved_mapping.items():
                    if fid in self.mapping and isinstance(path,str): self.mapping[fid]=path
            self.vars['timeline_source'].set(saved.get('timeline_source','') if isinstance(saved.get('timeline_source',''),str) else '')
            self.pathlabel.configure(text=str(session.path))
            self.track.configure(values=[f'{i+1}: {name}' for i,(name,clips) in enumerate(session.tracks)])
            self.track.current(0)
            if saved.get('track') in self.track.cget('values'): self.vars['track'].set(saved['track'])
            self.info.delete('1.0','end'); self.info.insert('end',f'会话字段（不是源编码）：{json.dumps(session.attributes,ensure_ascii=False)}\n时间单位：{self.vars["unit"].get()}。源声道/位深/码率将用 ffprobe 实测。\n'+ '\n'.join(session.warnings))
            self.refresh()
            self.restoring=False; self.save_settings()
        except Exception as e:
            self.restoring=False
            messagebox.showerror('工程读取失败',str(e))
    def change_track(self):
        if self.vars['timeline_source'].get():
            self.vars['timeline_source'].set('')
            self.logline('切换音轨已清除烘焙混缩，请为当前音轨重新选择正确混缩。')
        self.refresh(); self.save_settings()
    def clips(self): return self.session.tracks[self.track.current()][1] if self.session else []
    def refresh(self):
        self.table.delete(*self.table.get_children())
        for i,c in enumerate(self.clips()):
            path=self.mapping.get(c.file_id,'')
            self.table.insert('', 'end',iid=str(i),values=(c.name,f'{c.start:.6f}',f'{c.source_in:.6f}',f'{c.duration:.6f}',f'{c.file_id}: {Path(path).name if path else "未映射"}',c.issue or ('待导出 / 已烘焙' if self.vars['timeline_source'].get() else '待导出（效果见查询）' if path else '缺少音源')))
    def map_source(self):
        chosen=self.table.selection()
        if not chosen: messagebox.showinfo('映射','请选中一个片段；同 fileID 的全部片段会一起映射。'); return
        p=filedialog.askopenfilename(initialdir=self.dialog_dir('source'),title='选择时间对齐的替代源文件')
        if not p: return
        self.remember_path('source',p)
        ids={self.clips()[int(i)].file_id for i in chosen}
        for fid in ids: self.mapping[fid]=p
        self.save_settings(); self.refresh(); self.logline(f'映射 fileID {sorted(ids)} → {p}（替代源须与原录音时间对齐）')
    def show_mapping(self):
        if not self.session: return
        snapshot=dict(self.mapping); originals=dict(self.session.files)
        self.lock(True); self.cancel.clear()
        def worker():
            try:
                for fid,p in snapshot.items():
                    self.q.put(('log',f'fileID={fid}；SESX 引用={originals[fid]["original"]}；实际={p or "未映射"}'))
                    if p:
                        try: self.q.put(('log',json.dumps(probe(p,self.cancel),ensure_ascii=False)))
                        except Exception as e: self.q.put(('log',str(e)))
            finally: self.q.put(('done','属性检查完成'))
        threading.Thread(target=worker,daemon=True).start()
    def pick_output(self):
        p=filedialog.askdirectory(initialdir=self.dialog_dir('output'))
        if p: self.vars['output'].set(p); self.remember_path('output',p,True)
    def pick_mix(self):
        p=filedialog.askopenfilename(initialdir=self.dialog_dir('mix'),title='选择从会话 0 秒开始的已烘焙整轨混缩')
        if p: self.vars['timeline_source'].set(p); self.remember_path('mix',p); self.refresh()
    def options(self):
        return Options(**{f.name:(float(self.vars[f.name].get()) if f.name in ('peak','lufs','ceiling') else self.vars[f.name].get()) for f in dataclasses.fields(Options)})
    def start(self,selected):
        if not self.session or self.busy: return
        try:
            opt=self.options(); opt.validate()
            folder=Path(self.vars['output'].get()).expanduser()
            if not self.vars['output'].get().strip(): raise ExportError('请选择输出目录')
            folder.mkdir(parents=True,exist_ok=True)
            ids=list(self.table.selection()) if selected else list(self.table.get_children())
            if not ids: raise ExportError('没有要导出的片段')
            clips=[(iid,self.clips()[int(iid)]) for iid in ids]
            session=self.session; mapping=dict(self.mapping)
            executable('ffmpeg'); executable('ffprobe')
        except Exception as e: messagebox.showerror('设置错误',str(e)); return
        self.cancel.clear(); self.lock(True)
        def worker():
            used=set(); ok=failed=skipped=0
            logpath=folder/('export-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.jsonl')
            try:
                with logpath.open('x',encoding='utf-8') as log:
                    log.write(json.dumps({'session':str(session.path),'session_attributes':session.attributes,'time_unit':self.vars_snapshot_unit,'limitations':session.warnings},ensure_ascii=False)+'\n')
                    for count,(iid,c) in enumerate(clips,1):
                        if self.cancel.is_set(): break
                        try:
                            result=export_clip(session,c,mapping,folder,opt,used,self.cancel)
                            status='完成' if result['status']=='ok' else '跳过重名'
                            if result['status']=='ok': ok+=1
                            else: skipped+=1
                        except Cancelled: break
                        except Exception as e:
                            failed+=1; status='失败：'+str(e); result={'status':'error','name':c.name,'error':str(e)}
                        log.write(json.dumps(result,ensure_ascii=False)+'\n'); log.flush()
                        self.q.put(('status',(iid,status))); self.q.put(('log',f'{count}/{len(clips)} {c.name}：{status}'))
                        for warning in result.get('warnings',[]): self.q.put(('log',warning))
                self.q.put(('log',f'日志：{logpath}'))
            except Exception as e: self.q.put(('log','批次异常：'+str(e)))
            finally: self.q.put(('done',f'{"已取消；" if self.cancel.is_set() else ""}成功 {ok}，跳过 {skipped}，失败 {failed}'))
        self.vars_snapshot_unit=self.vars['unit'].get()
        threading.Thread(target=worker,daemon=True).start()
    def listen(self):
        chosen=self.table.selection()
        if not chosen or not self.session: return
        c=self.clips()[int(chosen[0])]; src=self.mapping.get(c.file_id)
        if not src: messagebox.showerror('试听失败','缺少音源'); return
        self.cancel.clear(); self.lock(True)
        def worker():
            try:
                if self.play_tmp: self.play_tmp.cleanup()
                self.play_tmp=tempfile.TemporaryDirectory(prefix='sesx-preview-')
                out=Path(self.play_tmp.name)/'preview.wav'; info=probe(src,self.cancel)
                if c.issue: raise ExportError(c.issue)
                if c.source_out>info['duration']+1/info['rate']: raise ExportError('试听裁剪越界')
                ff(src,out,f'atrim=start_sample={round(c.source_in*info["rate"])}:end_sample={round(c.source_out*info["rate"])},asetpts=PTS-STARTPTS',cancel=self.cancel)
                player=shutil.which('afplay')
                if not player: raise ExportError('试听需要 macOS afplay')
                run([player,out],self.cancel)
            except Exception as e: self.q.put(('log','试听：'+str(e)))
            finally: self.q.put(('done','原始片段试听结束（未应用导出设置）'))
        threading.Thread(target=worker,daemon=True).start()
    def save_csv(self):
        p=filedialog.asksaveasfilename(initialdir=self.dialog_dir('csv'),defaultextension='.csv')
        if p:
            self.remember_path('csv',p)
            with open(p,'w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f); w.writerow(['片段名','时间轴起点秒','时间轴终点秒','源起点秒','源终点秒','fileID','源文件','问题'])
                for c in self.clips(): w.writerow([c.name,c.start,c.end,c.source_in,c.source_out,c.file_id,self.mapping.get(c.file_id,''),c.issue])
    def poll(self):
        try:
            while True:
                kind,data=self.q.get_nowait()
                if kind=='log': self.logline(data)
                elif kind=='status':
                    iid,status=data; self.table.set(iid,'status',status)
                elif kind=='done': self.lock(False); self.progress.configure(text=data); self.logline(data)
        except queue.Empty: pass
        self.root.after(100,self.poll)
    def close(self):
        if self.busy:
            self.cancel.set(); self.logline('正在取消，请稍候再关闭窗口。'); return
        self.save_settings()
        if self.play_tmp: self.play_tmp.cleanup()
        self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); App(root); root.mainloop()
