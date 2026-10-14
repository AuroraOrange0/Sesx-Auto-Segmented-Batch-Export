"""Atomic, debounced GUI preferences. Override path for portable use or tests."""
import json, os, tempfile
from pathlib import Path
class Preferences:
    def __init__(self,path=None):
        self.path=Path(path or os.environ.get('SESX_SETTINGS_PATH') or Path.home()/'Library/Application Support/SESX Exporter/settings.json')
        self.error=''; self.data={}
        try:
            if self.path.is_file():
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or data.get('version')!=1: raise ValueError('配置结构或版本无效')
                self.data=data
        except (OSError,ValueError) as e: self.error=f'配置读取失败，使用默认值：{e}'
    def write(self,data):
        tmp=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=self.path.parent,prefix='.settings-',suffix='.json')
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(dict(data,version=1),f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,self.path); self.data=dict(data,version=1); self.error=''
            return True
        except OSError as e:
            self.error=f'配置保存失败：{e}'; return False
        finally:
            if tmp and Path(tmp).exists(): Path(tmp).unlink()
    def initialdir(self,kind):
        value=self.data.get('directories',{}).get(kind,'')
        if not isinstance(value,str): value=''
        try: p=Path(value).expanduser() if value else Path.home()
        except (OSError,ValueError): p=Path.home()
        while not p.is_dir() and p!=p.parent: p=p.parent
        return str(p)
